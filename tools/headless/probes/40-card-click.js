// Expects the date menu OPEN. Emits 'clicked' on the card button (St.Button -> vfunc_clicked),
// then reports: date menu open?, indicator popup open?. Closes the popup afterwards.
(() => { const d = Main.panel.statusArea.dateMenu; const ind = Main.panel.statusArea['ai-usage'];
let card = null; const find = a => { const sc = a.get_style_class_name && a.get_style_class_name();
  if (sc && sc.indexOf('ai-usage-card') >= 0) { card = a; return; } a.get_children().forEach(find); };
find(d.menu.box);
if (!card) return 'NO CARD';
const before = { dateMenuOpen: d.menu.isOpen, popupOpen: ind.menu.isOpen };
card.emit('clicked', 1);
const after = { dateMenuOpen: d.menu.isOpen, popupOpen: ind.menu.isOpen };
const res = JSON.stringify({ before, after, cardClass: card.get_style_class_name() });
if (ind.menu.isOpen) ind.menu.close(false);
return res; })()
