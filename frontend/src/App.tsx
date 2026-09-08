import { FormEvent, useEffect, useState } from "react";
import { api, Project, User } from "./api";

function Brand() {
  return (
    <div className="brand">
      <span className="brand-mark">D</span>
      <span>
        <strong>Dars Manager</strong>
        <small>Content workspace</small>
      </span>
    </div>
  );
}

function Login({ onAuthenticated }: { onAuthenticated: (user: User) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError("");
    try {
      onAuthenticated(await api.login(email, password));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Connexion impossible.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="login-shell">
      <section className="login-story">
        <Brand />
        <div>
          <span className="eyebrow">Votre studio éditorial</span>
          <h1>Du cours brut au contenu prêt à diffuser.</h1>
          <p>
            Transcrivez, découpez et préparez vos audios dans un espace de travail
            simple, confidentiel et conçu pour évoluer.
          </p>
        </div>
        <p className="privacy-note">Vos médias sont supprimés automatiquement après traitement.</p>
      </section>
      <section className="login-panel">
        <form className="login-card" onSubmit={submit}>
          <span className="eyebrow">Accès sécurisé</span>
          <h2>Bienvenue</h2>
          <p>Connectez-vous à votre espace Dars Manager.</p>
          <label>
            Adresse e-mail
            <input
              autoComplete="email"
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="vous@exemple.com"
              required
            />
          </label>
          <label>
            Mot de passe
            <input
              autoComplete="current-password"
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
          </label>
          {error && <p className="form-error">{error}</p>}
          <button className="button primary" disabled={loading} type="submit">
            {loading ? "Connexion…" : "Se connecter"}
          </button>
        </form>
      </section>
    </main>
  );
}

function Dashboard({ user, onLogout }: { user: User; onLogout: () => void }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState("");

  useEffect(() => {
    api.projects()
      .then(setProjects)
      .catch((reason) => setError(reason.message))
      .finally(() => setLoading(false));
  }, []);

  async function createProject(event: FormEvent) {
    event.preventDefault();
    if (!title.trim()) return;
    setCreating(true);
    setError("");
    try {
      const project = await api.createProject(title.trim(), "");
      setProjects((current) => [project, ...current]);
      setTitle("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Création impossible.");
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Brand />
        <nav>
          <a className="active" href="#dashboard"><span>⌂</span> Vue d’ensemble</a>
          <a href="#projects"><span>▱</span> Mes projets</a>
          <a href="#tools"><span>◇</span> Outils</a>
          <a href="#jobs"><span>↻</span> Traitements</a>
          <a href="#settings"><span>⚙</span> Paramètres</a>
        </nav>
        <div className="sidebar-user">
          <span className="avatar">{user.display_name.charAt(0).toUpperCase()}</span>
          <span><strong>{user.display_name}</strong><small>{user.email}</small></span>
          <button aria-label="Se déconnecter" onClick={onLogout}>↗</button>
        </div>
      </aside>

      <main className="workspace" id="dashboard">
        <header className="workspace-header">
          <div>
            <span className="eyebrow">Vue d’ensemble</span>
            <h1>Bonjour {user.display_name.split(" ")[0]}</h1>
            <p>Transformez votre prochain cours en contenus structurés.</p>
          </div>
          <span className="status-pill"><i /> Tous les services sont opérationnels</span>
        </header>

        <section className="quick-start">
          <div>
            <span className="eyebrow light">Nouveau traitement</span>
            <h2>Commencez avec un fichier audio</h2>
            <p>Créez un projet, puis transcrivez et découpez votre cours.</p>
          </div>
          <form onSubmit={createProject}>
            <input
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="Titre du nouveau projet"
              maxLength={180}
              required
            />
            <button className="button accent" disabled={creating} type="submit">
              {creating ? "Création…" : "Créer le projet"}
            </button>
          </form>
        </section>

        <section className="section-heading" id="projects">
          <div><span className="eyebrow">Projets</span><h2>Vos espaces de travail</h2></div>
          <span>{projects.length} projet{projects.length > 1 ? "s" : ""}</span>
        </section>

        {error && <p className="form-error notice">{error}</p>}
        {loading ? (
          <div className="empty-state"><span className="loader" /><p>Chargement des projets…</p></div>
        ) : projects.length === 0 ? (
          <div className="empty-state">
            <span className="empty-icon">＋</span>
            <h3>Votre premier projet commence ici</h3>
            <p>Donnez-lui un titre ci-dessus. Vous pourrez ensuite ajouter votre audio.</p>
          </div>
        ) : (
          <div className="project-grid">
            {projects.map((project) => (
              <article className="project-card" key={project.id}>
                <span className="project-icon">◫</span>
                <div><h3>{project.title}</h3><p>{project.description || "Projet audio"}</p></div>
                <time>{new Intl.DateTimeFormat("fr", { dateStyle: "medium" }).format(new Date(project.updated_at))}</time>
                <button aria-label={`Ouvrir ${project.title}`}>→</button>
              </article>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    api.me().then(setUser).catch(() => setUser(null)).finally(() => setChecking(false));
  }, []);

  async function logout() {
    await api.logout().catch(() => undefined);
    setUser(null);
  }

  if (checking) return <div className="boot"><Brand /><span className="loader" /></div>;
  return user ? <Dashboard user={user} onLogout={logout} /> : <Login onAuthenticated={setUser} />;
}
