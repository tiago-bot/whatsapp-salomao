export function formatMessage(content) {
  const escape = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[char]);
  const links = [];
  let text = escape(content).replace(/TRANSFERIR_SUPORTE|&lt;REQUIRES_ESCALATION&gt;/g, '');
  text = text.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, (_, label, href) => {
    const token = '\uE000' + links.length + '\uE001';
    links.push('<a href="' + href + '" target="_blank" rel="noopener noreferrer" style="color:#1d4ed8;text-decoration:underline;overflow-wrap:anywhere">' + label + '</a>');
    return token;
  });
  text = text
    .replace(/^#{1,6}[ \t]+(.+)$/gm, '<strong>$1</strong>')
    .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[\s(])\*([^*\n]+)\*(?=$|[\s.,!?:;)])/g, '$1<strong>$2</strong>')
    .replace(/^&gt;[ \t]?(.*)$/gm, '<span style="display:block;border-left:3px solid #cbd5e1;padding-left:12px;color:#475569">$1</span>')
    .replace(/\n/g, '<br>');
  links.forEach((link, index) => { text = text.replace('\uE000' + index + '\uE001', link); });
  return text;
}
