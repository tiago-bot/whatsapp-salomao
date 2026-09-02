"""Run only isolated regressions; legacy test scripts can contact real services."""
import os
import socket
import ipaddress
import unittest
from unittest.mock import patch


if __name__ == "__main__":
    os.environ.update({
        "OPENAI_API_KEY": "test-key", "PINECONE_API_KEY": "test-key",
        "PINECONE_HOST": "https://test.svc.example.com",
        "SUPABASE_URL": "https://test.supabase.co", "SUPABASE_KEY": "test-key",
        "HUBSPOT_ACCESS_TOKEN": "test-key", "HUBSPOT_POLLING_ENABLED": "false",
        "AGNO_TELEMETRY": "false",
    })
    original_connect = socket.socket.connect
    def local_only(sock, address):
        try:
            if ipaddress.ip_address(address[0]).is_loopback:
                return original_connect(sock, address)
        except (ValueError, TypeError):
            pass
        raise AssertionError("Offline tests cannot access the external network")

    with patch.object(socket.socket, "connect", local_only):
        suite = unittest.defaultTestLoader.loadTestsFromNames([
            "test_scope_regressions", "test_grounded_answers", "test_whatsapp", "test_api_contract",
        ])
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
