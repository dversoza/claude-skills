// Jira backlog extraction — BROWSER FALLBACK path.
//
// PRIMARY path is the Atlassian CLI (`acli`): see SKILL.md. Prefer
//   acli jira workitem search --jql "project = <KEY> ..." --fields "key,status,summary,issuetype,assignee,priority" --json --paginate
// for the lightweight index/status refresh, and `acli jira workitem view <KEY> --json`
// for rich fields (description/comments/attachments). Use THIS script only when acli
// is not installed / not authenticated, or for bulk rich extraction across the whole
// open backlog (acli's --fields allowlist omits comments/attachments and clips bodies).
//
// Run inside the Claude-in-Chrome javascript_tool on a tab already logged in to the
// target Jira Cloud instance. Same-origin fetch is authenticated by the session
// cookie. Assembles all data and downloads it as one JSON file (robust for large
// payloads; keeps data off the model context).
//
// Set PROJECT before running. Do the whole thing in ONE javascript_tool call so the
// single allowed auto-download fires with the complete payload. If a download was
// already triggered on this page, reload the page first (clears window state).

const PROJECT = 'VD'; // <-- change per project
const base = window.location.origin;
const j = (u, o = {}) =>
  fetch(u, { credentials: 'include', headers: { 'Accept': 'application/json', 'Content-Type': 'application/json' }, ...o }).then(r => r.json());

// --- custom field ids (vary per instance) ---
const fields = await j(base + '/rest/api/3/field');
const byName = Object.fromEntries(fields.map(f => [f.name, f.id]));
const M = {
  sprintId: byName['Sprint'],
  spId: byName['Story Points'] || byName['Story point estimate'],
  epicId: byName['Epic Link'] || byName['Parent Link'],
  startId: byName['Start date'],
};
const FLDS = ['summary','issuetype','status','priority','assignee','reporter','created','updated','resolution','resolutiondate','labels','components','fixVersions','parent','subtasks','issuelinks','description','comment','attachment','duedate', M.sprintId, M.spId, M.epicId, M.startId, 'customfield_10016'].filter(Boolean);

// --- rendered HTML -> clean text, preserving links as "text <url>" ---
function htmlToText(html) {
  if (!html) return '';
  const doc = new DOMParser().parseFromString(html, 'text/html');
  doc.querySelectorAll('a[href]').forEach(a => {
    const href = a.getAttribute('href') || '', t = (a.textContent || '').trim();
    if (href && !t.includes(href) && !href.startsWith('mailto:' + t)) a.textContent = t + ' <' + href + '>';
  });
  doc.querySelectorAll('li').forEach(li => li.insertBefore(doc.createTextNode('• '), li.firstChild));
  doc.querySelectorAll('p,div,br,tr,h1,h2,h3,h4,h5,h6,li').forEach(el => el.appendChild(doc.createTextNode('\n')));
  doc.querySelectorAll('td,th').forEach(el => el.appendChild(doc.createTextNode(' | ')));
  return (doc.body.textContent || '').replace(/\n{3,}/g, '\n\n').replace(/[ \t]+\n/g, '\n').trim();
}
const nm = o => (o && o.displayName) ? o.displayName : (o ? (o.name || o.value || String(o)) : null);
const sprintOf = a => Array.isArray(a) ? a.map(s => ({ name: s.name, state: s.state })) : (a ? [{ name: a.name, state: a.state }] : []);

function compact(it) {
  const f = it.fields, r = it.renderedFields || {};
  const comments = ((f.comment && f.comment.comments) || []).map((c, i) => ({
    by: nm(c.author), at: c.created,
    text: htmlToText((r.comment && r.comment.comments && r.comment.comments[i] && r.comment.comments[i].body) || ''),
  }));
  const attachments = (f.attachment || []).map(a => ({ name: a.filename, size: a.size, mime: a.mimeType, by: nm(a.author), at: a.created, url: a.content }));
  const links = (f.issuelinks || []).map(l => {
    const out = l.outwardIssue, inw = l.inwardIssue, o = out || inw;
    return { rel: out ? l.type.outward : l.type.inward, dir: out ? 'out' : 'in', key: o ? o.key : null, summary: o ? o.fields.summary : null, status: o && o.fields.status ? o.fields.status.name : null };
  });
  return {
    key: it.key, id: it.id, type: nm(f.issuetype), summary: f.summary,
    status: f.status ? f.status.name : null, statusCat: f.status && f.status.statusCategory ? f.status.statusCategory.name : null,
    priority: nm(f.priority), assignee: nm(f.assignee), reporter: nm(f.reporter),
    created: f.created, updated: f.updated, resolved: f.resolutiondate, resolution: nm(f.resolution),
    labels: f.labels || [], components: (f.components || []).map(nm), fixVersions: (f.fixVersions || []).map(nm),
    sprint: sprintOf(f[M.sprintId]), points: f[M.spId] != null ? f[M.spId] : (f.customfield_10016 != null ? f.customfield_10016 : null),
    epic: f[M.epicId] || null,
    parent: f.parent ? { key: f.parent.key, summary: f.parent.fields.summary, status: f.parent.fields.status ? f.parent.fields.status.name : null } : null,
    duedate: f.duedate, startdate: f[M.startId] || null,
    description: htmlToText(r.description || ''),
    subtasks: (f.subtasks || []).map(st => ({ key: st.key, summary: st.fields.summary, status: st.fields.status ? st.fields.status.name : null })),
    links, comments, attachments,
  };
}

async function paged(jql, fieldsArr, expand) {
  let out = [], token = null, guard = 0;
  do {
    const body = { jql, fields: fieldsArr, maxResults: 100 };
    if (expand) body.expand = expand;
    if (token) body.nextPageToken = token;
    const p = await j(base + '/rest/api/3/search/jql', { method: 'POST', body: JSON.stringify(body) });
    out = out.concat(p.issues || []);
    token = p.isLast ? null : p.nextPageToken;
  } while (token && ++guard < 25);
  return out;
}

// 1) open issues (full)   2) all epics (full)   3) whole-project lightweight index
const issues = (await paged(`project = ${PROJECT} AND statusCategory != Done ORDER BY created ASC`, FLDS, 'renderedFields')).map(compact);
const epics = (await paged(`project = ${PROJECT} AND issuetype = Epic ORDER BY created ASC`, FLDS, 'renderedFields')).map(compact);
const idxFields = ['summary','issuetype','status','parent','resolutiondate','resolution','updated','created','assignee','labels', M.epicId, M.spId, 'customfield_10016'].filter(Boolean);
const allRaw = await paged(`project = ${PROJECT} ORDER BY created ASC`, idxFields, null);
const allIndex = allRaw.map(it => {
  const f = it.fields;
  return { key: it.key, type: nm(f.issuetype), status: f.status ? f.status.name : null, statusCat: f.status && f.status.statusCategory ? f.status.statusCategory.name : null,
           epic: f[M.epicId] || null, parent: f.parent ? f.parent.key : null, assignee: nm(f.assignee),
           points: f[M.spId] != null ? f[M.spId] : (f.customfield_10016 != null ? f.customfield_10016 : null),
           created: f.created, updated: f.updated, resolved: f.resolutiondate, resolution: nm(f.resolution), labels: f.labels || [], summary: f.summary };
});
// attach epic child-completion stats
for (const e of epics) {
  const kids = allIndex.filter(x => x.epic === e.key || x.parent === e.key);
  e.children = kids.map(k => ({ key: k.key, type: k.type, status: k.status, statusCat: k.statusCat, resolved: k.resolved, summary: k.summary }));
  e.childStats = { total: kids.length, done: kids.filter(k => k.statusCat === 'Done').length, cancelled: kids.filter(k => k.status === 'Canceled').length, open: kids.filter(k => k.statusCat !== 'Done').length };
}

const payload = { meta: { project: PROJECT, counts: { issuesNonDone: issues.length, epics: epics.length, allIndex: allIndex.length } }, issues, epics, allIndex };

// single Blob download -> ~/Downloads
const blob = new Blob([JSON.stringify(payload)], { type: 'application/json' });
const url = URL.createObjectURL(blob);
const a = document.createElement('a');
a.href = url; a.download = 'backlog_dump.json';
document.body.appendChild(a); a.click(); a.remove();
setTimeout(() => URL.revokeObjectURL(url), 6000);

JSON.stringify(payload.meta.counts); // returned to confirm; the data itself is in the download
