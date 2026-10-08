// Counts 'Consommation IA' tray sources and their notifications (text + urgency).
(() => { const srcs = Main.messageTray.getSources().filter(s => s.title === 'Consommation IA');
const notes = []; srcs.forEach(s => s.notifications.forEach(n => notes.push({ body: n.bannerBodyText, urgency: n.urgency })));
return JSON.stringify({ sources: srcs.length, notifications: notes.length, items: notes }); })()
