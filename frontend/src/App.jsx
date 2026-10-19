import { Fragment, useCallback, useEffect, useState } from "react";

export default function App({ kc, api }) {
  const roles = kc.tokenParsed?.realm_access?.roles ?? [];
  const tabs = [...(roles.includes("user") ? ["CVs", "Analysis", "Match JD"] : []), ...(roles.includes("admin") ? ["Users"] : [])];
  const [tab, setTab] = useState(tabs[0]);

  return (
    <>
      <header>
        <h1>CV Parser</h1>
        <nav>
          {tabs.map((t) => (
            <button key={t} className={t === tab ? "tab active" : "tab"} onClick={() => setTab(t)}>{t}</button>
          ))}
        </nav>
        <span className="muted">
          {kc.tokenParsed?.preferred_username} · {roles.filter((r) => r === "admin" || r === "user").join(", ")}
        </span>
        <button onClick={() => kc.logout({ redirectUri: window.location.origin })}>Log out</button>
      </header>
      <main>
        {!tab && <p>Your account has no role yet. Ask an admin to give you the "user" or "admin" role.</p>}
        {tab === "CVs" && <Cvs api={api} />}
        {tab === "Analysis" && <Analysis api={api} />}
        {tab === "Match JD" && <Match api={api} />}
        {tab === "Users" && <Users api={api} />}
      </main>
    </>
  );
}

const ACCEPT = ".pdf,.doc,.docx";

function Cvs({ api }) {
  const [cvs, setCvs] = useState([]);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(null);
  const [msg, setMsg] = useState("");
  const load = useCallback(() => api("/cvs").then(setCvs).catch((e) => setMsg(e.message)), [api]);
  useEffect(() => { load(); }, [load]);

  // poll while the worker is still parsing something
  const busy = cvs.some((c) => c.status === "pending" || c.status === "processing");
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [busy, load]);

  const pick = async (e) => {
    const files = [...e.target.files];
    e.target.value = "";
    if (!files.length) return;
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    setMsg(`Uploading ${files.length} file(s)…`);
    try {
      const r = await api("/cvs", { method: "POST", body: fd });
      setMsg(`Queued ${r.length} CV(s) for parsing.`);
      load();
    } catch (err) {
      setMsg(err.message);
    }
  };

  const needle = q.toLowerCase();
  const shown = cvs.filter((c) =>
    [c.name, c.email, c.mobile, c.location, c.filename, ...c.skills].join(" ").toLowerCase().includes(needle)
  );

  return (
    <>
      <section className="card toolbar">
        <label className="btn">Upload a CV<input type="file" hidden accept={ACCEPT} onChange={pick} /></label>
        <label className="btn">Upload multiple CVs<input type="file" hidden multiple accept={ACCEPT} onChange={pick} /></label>
        <span className="muted">PDF, DOC or DOCX, up to 10 MB each. Scanned PDFs are OCR'd.</span>
        <input className="search" type="search" placeholder="Search name, skill, location…" aria-label="Search CVs"
          value={q} onChange={(e) => setQ(e.target.value)} />
      </section>
      {msg && <p className="msg">{msg}</p>}
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>Name</th><th>Email</th><th>Mobile</th><th>Location</th><th>Key skills</th><th>Exp.</th><th>Status</th><th>Uploaded</th></tr>
          </thead>
          <tbody>
            {shown.map((c) => (
              <Fragment key={c.id}>
                <tr className="row" onClick={() => setOpen(open === c.id ? null : c.id)}>
                  <td><b>{c.name || "—"}</b><div className="muted small">{c.filename}</div></td>
                  <td>{c.email || "—"}</td>
                  <td>{c.mobile || "—"}</td>
                  <td>{c.location || "—"}</td>
                  <td>
                    {c.skills.slice(0, 5).map((s) => <span className="chip" key={s}>{s}</span>)}
                    {c.skills.length > 5 && <span className="muted small">+{c.skills.length - 5}</span>}
                  </td>
                  <td>{c.experience_years != null ? `${c.experience_years} y` : "—"}</td>
                  <td><span className={`status ${c.status}`}>{c.status}</span></td>
                  <td className="small">{new Date(c.created_at).toLocaleString()}<div className="muted">{c.uploaded_by}</div></td>
                </tr>
                {open === c.id && (
                  <tr><td colSpan={8}><Details cv={c} /></td></tr>
                )}
              </Fragment>
            ))}
            {!shown.length && <tr><td colSpan={8} className="muted center">No CVs yet. Upload some above.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}

function Details({ cv }) {
  if (cv.status === "failed") return <p className="error">Parsing failed: {cv.error}</p>;
  if (cv.status !== "done") return <p className="muted">Still parsing…</p>;
  const d = cv.details || {};
  const list = (v) => (Array.isArray(v) ? v.join(" · ") : v) || "—";
  return (
    <dl className="details">
      <dt>Current role</dt><dd>{d.current_title || "—"}</dd>
      <dt>Summary</dt><dd>{d.summary || "—"}</dd>
      <dt>Education</dt><dd>{list(d.education)}</dd>
      <dt>Companies</dt><dd>{list(d.companies)}</dd>
      <dt>All skills</dt><dd>{cv.skills.map((s) => <span className="chip" key={s}>{s}</span>)}</dd>
    </dl>
  );
}

function Analysis({ api }) {
  const [a, setA] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => { api("/analysis").then(setA).catch((e) => setErr(e.message)); }, [api]);
  if (err) return <p className="error">{err}</p>;
  if (!a) return <p className="muted">Loading…</p>;
  const s = a.summary;
  return (
    <>
      <div className="tiles">
        <Tile label="CVs uploaded" value={s.total} />
        <Tile label="Parsed" value={s.parsed} />
        <Tile label="Avg. experience" value={s.avg_experience != null ? `${s.avg_experience} yrs` : "—"} />
        <Tile label="Distinct locations" value={s.locations} />
      </div>
      <div className="grid">
        <Bars title="Top skills" rows={a.top_skills} k="skill" />
        <Bars title="Locations" rows={a.locations} k="location" />
        <Bars title="Experience" rows={a.experience} k="bucket" />
        <Bars title="Processing status" rows={a.status} k="status" />
      </div>
    </>
  );
}

const Tile = ({ label, value }) => (
  <div className="card tile"><div className="muted small">{label}</div><div className="big">{value}</div></div>
);

function Bars({ title, rows, k }) {
  const max = Math.max(1, ...rows.map((r) => r.count));
  return (
    <section className="card">
      <h3>{title}</h3>
      {rows.length ? rows.map((r) => (
        <div className="bar" key={r[k]}>
          <span title={r[k]}>{r[k]}</span>
          <div><i style={{ width: `${(r.count / max) * 100}%` }} /></div>
          <b>{r.count}</b>
        </div>
      )) : <p className="muted">No data yet.</p>}
    </section>
  );
}

function Match({ api }) {
  const [jd, setJd] = useState("");
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const run = async (e) => {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      setRes(await api("/match", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ jd }),
      }));
    } catch (x) {
      setErr(x.message);
    } finally {
      setBusy(false);
    }
  };

  const req = res?.requirements;
  return (
    <>
      <form className="card form" onSubmit={run}>
        <h3>Find the 5 best-fit candidates for a job</h3>
        <label>Job description
          <textarea rows={8} required minLength={30} value={jd} onChange={(e) => setJd(e.target.value)}
            placeholder="Paste the job description: role, must-have skills, years of experience…" />
        </label>
        <button className="btn" disabled={busy}>{busy ? "Reading the JD…" : "Find best matches"}</button>
        {err && <p className="error">{err}</p>}
      </form>

      {req && (
        <section className="card reqs">
          <h3>{req.title || "Requirements"}</h3>
          <p><span className="muted">Must have: </span>{req.required_skills.map((s) => <span className="chip" key={s}>{s}</span>)}</p>
          {!!req.nice_to_have_skills.length && (
            <p><span className="muted">Nice to have: </span>{req.nice_to_have_skills.map((s) => <span className="chip soft" key={s}>{s}</span>)}</p>
          )}
          <p><span className="muted">Experience: </span>{req.min_experience_years != null ? `${req.min_experience_years}+ yrs` : "not stated"}</p>
        </section>
      )}

      {res && (res.candidates.length ? res.candidates.map((c, i) => (
        <section className="card cand" key={c.id}>
          <div className="rank">#{i + 1}</div>
          <div className="cand-body">
            <b>{c.name || c.filename}</b>
            <span className="muted"> · {[c.current_title, c.location].filter(Boolean).join(" · ")}</span>
            <div className="muted small">
              {[c.email, c.mobile, c.experience_years != null && `${c.experience_years} yrs experience`].filter(Boolean).join(" · ")}
            </div>
            <div className="skills-line">
              {c.matched_skills.map((s) => <span className="chip ok" key={s}>✓ {s}</span>)}
              {c.nice_matched.map((s) => <span className="chip soft" key={s}>+ {s}</span>)}
              {c.missing_skills.map((s) => <span className="chip miss" key={s}>✗ {s}</span>)}
            </div>
          </div>
          <div className="score">
            <div className="big">{c.score}</div>
            <div className="meter" aria-hidden="true"><i style={{ width: `${c.score}%` }} /></div>
            <div className="muted small">fit score</div>
          </div>
        </section>
      )) : <p className="muted">No parsed CV has any of the must-have skills.</p>)}
      {res && <p className="muted small">Fit score = 60% must-have skills + 10% nice-to-have skills + 30% experience vs. required.</p>}
    </>
  );
}

function Users({ api }) {
  const [users, setUsers] = useState([]);
  const [msg, setMsg] = useState("");
  const load = useCallback(() => api("/admin/users").then(setUsers).catch((e) => setMsg(e.message)), [api]);
  useEffect(() => { load(); }, [load]);

  const submit = async (e) => {
    e.preventDefault();
    const form = e.target;
    try {
      const u = await api("/admin/users", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(Object.fromEntries(new FormData(form))),
      });
      setMsg(`Created ${u.username} with role "${u.role}".`);
      form.reset();
      load();
    } catch (err) {
      setMsg(err.message);
    }
  };

  return (
    <div className="grid users">
      <form className="card form" onSubmit={submit}>
        <h3>Create user</h3>
        <label>Username<input name="username" required minLength={3} pattern="[A-Za-z0-9._\-]+" /></label>
        <label>Email<input name="email" type="email" required /></label>
        <label>First name<input name="first_name" required /></label>
        <label>Last name<input name="last_name" required /></label>
        <label>Password<input name="password" type="password" required minLength={8} /></label>
        <label>Role
          <select name="role">
            <option value="user">User: uploads and analyses CVs</option>
            <option value="admin">Admin: creates users</option>
          </select>
        </label>
        <button className="btn">Create user</button>
        {msg && <p className="msg">{msg}</p>}
      </form>
      <section className="card">
        <h3>Users</h3>
        <table>
          <thead><tr><th>Username</th><th>Name</th><th>Email</th><th>Roles</th><th>Created</th></tr></thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id}>
                <td>{u.username}</td><td>{u.name}</td><td>{u.email}</td>
                <td>{u.roles.map((r) => <span className="chip" key={r}>{r}</span>)}</td>
                <td className="small">{u.created ? new Date(u.created).toLocaleDateString() : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
