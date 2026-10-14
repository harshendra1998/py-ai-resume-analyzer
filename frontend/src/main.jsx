import { createRoot } from "react-dom/client";
import Keycloak from "keycloak-js";
import App from "./App.jsx";
import "./style.css";

const kc = new Keycloak({ url: "http://localhost:8080", realm: "cvapp", clientId: "cv-web" });

// fetch wrapper: refreshes the token and attaches it to every API call
async function api(path, opts = {}) {
  try {
    await kc.updateToken(30);
  } catch {
    return kc.login();
  }
  const r = await fetch(`/api${path}`, { ...opts, headers: { ...opts.headers, Authorization: `Bearer ${kc.token}` } });
  const body = await r.json().catch(() => null);
  if (!r.ok) {
    const d = body?.detail;
    throw new Error(Array.isArray(d) ? d.map((e) => `${e.loc.at(-1)}: ${e.msg}`).join("; ") : d || r.statusText);
  }
  return body;
}

kc.init({ onLoad: "login-required", pkceMethod: "S256", checkLoginIframe: false }).then(() =>
  createRoot(document.getElementById("root")).render(<App kc={kc} api={api} />)
);
