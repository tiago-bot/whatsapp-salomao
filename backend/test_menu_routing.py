"""Isolated regression for the option-2 event and recovery without CRM indexing."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch, AsyncMock

from delivery_store import DeliveryStore
import hubspot_service as hs
import hubspot_bot as bot_module
import menu_routing as routing
import main_hubspot as api


MENU = ('Olá! Escolha: 1️⃣ Suporte para a plataforma\n2️⃣ Dúvidas sobre a plataforma\n'
        '3️⃣ 2ª via de boleto\nDigite apenas o número da opção desejada (1, 2 ou 3).')


class MenuRoutingTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'db.sqlite3'
        self.store = DeliveryStore(self.path)
        self.router = routing.MenuRouter(self.store)
        self.now = datetime.now(timezone.utc) - timedelta(seconds=10)
        self.ticket = {'properties': {'hs_pipeline': hs.SALOMAO_PIPELINE,
            'hs_pipeline_stage': routing.WAITING_STAGE, 'hubspot_owner_id': hs.SALOMAO_ACTOR_ID[2:],
            hs.SALOMAO_ENTRY_PROPERTY: None}}
        self.thread = {'id': 't', 'associatedTicketId': 'ticket', 'originalChannelId': '1007',
                       'status': 'OPEN', 'assignedTo': hs.SALOMAO_ACTOR_ID}
        self.messages = [self.message('hi',-60,'oi'), self.message('menu',-50,MENU,False), self.message('two',-30,'2')]
        patch.object(hs,'get_ticket_by_id',side_effect=lambda *_: deepcopy(self.ticket)).start()
        patch.object(hs,'get_thread_by_id',side_effect=lambda *_: deepcopy(self.thread)).start()
        patch.object(hs,'get_thread_messages',side_effect=lambda *_,**kw: deepcopy(self.messages)).start()
        self.update = patch.object(hs.requests,'patch',side_effect=self.apply_route).start()
        self.addCleanup(patch.stopall)
        self.router.watch('t','ticket')

    def message(self,key,seconds,text,incoming=True):
        return {'id':key,'type':'MESSAGE','text':text,'createdAt':(self.now+timedelta(seconds=seconds)).isoformat(),
                'direction':'INCOMING' if incoming else 'OUTGOING',
                'senders':[{'actorId':'V-client' if incoming else hs.SALOMAO_ACTOR_ID}]}

    def apply_route(self,*a,**kw):
        self.ticket['properties'].update(kw['json']['properties'])
        self.ticket['properties'][hs.SALOMAO_ENTRY_PROPERTY] = self.now.isoformat()
        return MagicMock(status_code=200,json=lambda: deepcopy(self.ticket))

    def test_routes_choice_without_triagem_status_change_and_only_updates_stage(self):
        self.assertTrue(self.router.process('t','ticket'))
        self.assertEqual(self.update.call_args.kwargs['json'],{'properties':{'hs_pipeline_stage':hs.SALOMAO_STATUS}})
        with self.store._connect() as c:
            self.assertEqual(c.execute('SELECT message_id FROM menu_routes').fetchone()[0],'two')
        self.assertFalse(self.router.process('t','ticket'))
        self.update.assert_called_once()

    def test_watch_survives_restart_and_empty_crm_search(self):
        restarted = routing.MenuRouter(DeliveryStore(self.path))
        with patch.object(restarted,'discover',return_value=None):
            self.assertEqual(restarted.recover(),['ticket'])

    def test_watch_survives_early_menu_event_until_the_reply_arrives(self):
        choice = self.messages.pop()
        self.assertFalse(self.router.process('t','ticket'))
        self.messages.append(choice)
        self.assertTrue(self.router.process('t','ticket'))

    def test_watch_waits_for_owner_and_stage_written_after_webhook(self):
        self.ticket['properties'].update(hs_pipeline_stage='939271304',hubspot_owner_id='')
        self.assertFalse(self.router.process('t','ticket'))
        self.ticket['properties'].update(hs_pipeline_stage=routing.WAITING_STAGE,hubspot_owner_id=hs.SALOMAO_ACTOR_ID[2:])
        self.assertTrue(self.router.process('t','ticket'))

    def test_other_choices_and_number_inside_a_question_are_not_routes(self):
        for value in ['1','3','tenho 2 dúvidas','22','2 ou 3']:
            with self.subTest(value=value):
                self.messages[-1]['text']=value
                self.assertFalse(self.router.process('t','ticket'))
        self.update.assert_not_called()

    def test_missing_menu_or_changed_option_mapping_cannot_route(self):
        for text in ['Olá, como posso ajudar?', MENU.replace('2️⃣ Dúvidas','3️⃣ Dúvidas')]:
            self.messages[1]['text']=text
            self.assertFalse(self.router.process('t','ticket'))
        self.update.assert_not_called()

    def test_old_or_future_choices_do_not_reopen_service(self):
        for delta in [-2 * 86400, 3600]:
            with self.subTest(delta=delta):
                self.messages=[self.message('menu',delta-100,MENU,False), self.message('two',delta,'2')]
                self.assertFalse(self.router.process('t','ticket'))
        self.update.assert_not_called()

    def test_human_reply_new_selection_or_human_request_cancels_route(self):
        for message in [self.message('agent',-10,'Estou atendendo',False),self.message('change',-10,'1'),
                        self.message('human',-10,'quero falar com atendente humano')]:
            self.messages.append(message)
            self.assertFalse(self.router.process('t','ticket'))
            self.messages.pop()
        self.update.assert_not_called()

    def test_wrong_pipeline_owner_channel_association_and_closed_thread_do_not_route(self):
        variants=[('ticket','hs_pipeline','other'),('ticket','hubspot_owner_id','other'),
                  ('thread','originalChannelId','email'),('thread','associatedTicketId','other'),('thread','status','CLOSED')]
        for scope,key,value in variants:
            obj=self.ticket['properties'] if scope=='ticket' else self.thread
            original=obj[key]
            obj[key]=value
            self.assertFalse(self.router.process('t','ticket'))
            obj[key]=original
        self.update.assert_not_called()

    def test_takeover_between_reads_blocks_update(self):
        original=deepcopy(self.ticket)
        human=deepcopy(self.ticket)
        human['properties']['hubspot_owner_id']='human'
        with patch.object(hs,'get_ticket_by_id',side_effect=[original,human]):
            self.assertFalse(self.router.process('t','ticket'))
        self.update.assert_not_called()

    def test_reply_after_initial_read_blocks_update(self):
        changed=self.messages+[self.message('agent',-1,'Atendente',False)]
        with patch.object(hs,'get_thread_messages',side_effect=[self.messages,changed]):
            self.assertFalse(self.router.process('t','ticket'))
        self.update.assert_not_called()

    def test_failed_update_is_retried_without_forgetting_the_choice(self):
        self.update.side_effect=[MagicMock(status_code=503)]
        with self.assertRaises(hs.HubSpotReadError):
            self.router.process('t','ticket')
        self.update.side_effect=self.apply_route
        self.assertTrue(self.router.process('t','ticket'))

    def test_receipted_choice_cannot_be_reused_after_stage_moves_back(self):
        self.router.process('t','ticket')
        self.ticket['properties']['hs_pipeline_stage']=routing.WAITING_STAGE
        self.assertFalse(self.router.process('t','ticket'))
        self.update.assert_called_once()

    def test_webhook_routes_before_ai_eligibility_filter(self):
        order=[]
        with patch.object(api,'get_thread_by_id',return_value=self.thread), \
             patch.object(api,'menu_router') as router, \
             patch.object(api,'process_ticket_if_valid',new_callable=AsyncMock) as process:
            router.process.side_effect=lambda *a: order.append('route')
            process.side_effect=lambda *a: order.append('process')
            asyncio.run(api.process_message_if_valid('t'))
        self.assertEqual(order,['route','process'])

    def test_full_menu_to_greeting_wait_then_new_question(self):
        self.assertTrue(self.router.process('t','ticket'))
        agent=MagicMock()
        agent.process_message.return_value={'response':'Vamos verificar o cadastro.',
                                           'scope_policy_version':bot_module.SCOPE_POLICY_VERSION}
        bot=bot_module.HubSpotSalomaoBot(self.store,agent,debounce_seconds=0)
        with patch.object(bot_module,'get_ticket_by_id',side_effect=lambda *_:deepcopy(self.ticket)), \
             patch.object(bot_module,'get_thread_messages',side_effect=lambda *a,**kw:deepcopy(self.messages)), \
             patch.object(bot_module,'reply_to_visitor',return_value={'id':'greeting','createdAt':self.now.isoformat()}) as send:
            bot.process_thread('t','ticket')
            bot.process_thread('t','ticket')
            send.assert_called_once_with('t',bot.ENTRY_GREETING)
            agent.process_message.assert_not_called()
            self.messages.append(self.message('question',1,'Como cadastro um membro?'))
            send.return_value={'id':'answer','createdAt':(self.now+timedelta(seconds=2)).isoformat()}
            bot.process_thread('t','ticket')
            bot.process_thread('t','ticket')
            self.assertEqual(send.call_count,2)
            agent.process_message.assert_called_once()
