import { FormEvent, useEffect, useState } from "react";
import { api, Job, Project, User } from "./api";

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

const terminalStates = new Set(["completed", "cancelled", "failed", "expired"]);
const artifactLabels: Record<string, string> = {
  analysis: "Analyse JSON",
  audio: "Audio normalisé",
  cover: "Image de couverture",
  video: "Vidéo prête à publier",
};

function formatDuration(seconds?: number) {
  if (seconds === undefined) return "—";
  const rounded = Math.round(seconds);
  const minutes = Math.floor(rounded / 60);
  return `${minutes}:${String(rounded % 60).padStart(2, "0")}`;
}

function ProjectWorkspace({ project, onBack }: { project: Project; onBack: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [model, setModel] = useState("base");
  const [job, setJob] = useState<Job | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [actionPending, setActionPending] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!job || terminalStates.has(job.state)) return;
    const timer = window.setInterval(() => {
      api.job(job.id).then(setJob).catch((reason) => {
        setError(reason instanceof Error ? reason.message : "Suivi du traitement impossible.");
      });
    }, 1000);
    return () => window.clearInterval(timer);
  }, [job?.id, job?.state]);

  async function start(event: FormEvent) {
    event.preventDefault();
    if (!file) return;
    setSubmitting(true);
    setError("");
    try {
      setJob(await api.createJob(project.id, file, model, "fr"));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Import impossible.");
    } finally {
      setSubmitting(false);
    }
  }

  async function runAction(action: "pause" | "resume" | "cancel" | "delete") {
    if (!job) return;
    setActionPending(true);
    setError("");
    try {
      if (action === "delete") {
        await api.deleteJob(job.id);
        setJob(null);
        setFile(null);
      } else {
        const handler = {
          pause: api.pauseJob,
          resume: api.resumeJob,
          cancel: api.cancelJob,
        }[action];
        setJob(await handler(job.id));
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Action impossible.");
    } finally {
      setActionPending(false);
    }
  }

  const progress = Math.max(0, Math.min(100, Math.round((job?.progress ?? 0) * 100)));

  return (
    <section className="project-workspace">
      <button className="back-button" onClick={onBack}>← Tous les projets</button>
      <header className="project-header">
        <div>
          <span className="eyebrow">Outil audio</span>
          <h1>{project.title}</h1>
          <p>{project.description || "Transcription, découpage et création des supports de diffusion."}</p>
        </div>
        <span className="privacy-badge">Traitement temporaire · suppression automatique</span>
      </header>

      {!job ? (
        <form className="upload-card" onSubmit={start}>
          <div className="upload-intro">
            <span className="step-number">01</span>
            <div><h2>Importer le cours</h2><p>Choisissez le fichier audio à traiter sur votre machine.</p></div>
          </div>
          <label className={`drop-zone ${file ? "has-file" : ""}`}>
            <input
              type="file"
              accept="audio/*,.aac,.m4a,.mp3,.wav,.ogg,.flac"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              required
            />
            <span className="upload-icon">↑</span>
            <strong>{file ? file.name : "Sélectionner un fichier audio"}</strong>
            <small>{file ? `${(file.size / 1024 / 1024).toFixed(1)} Mo` : "AAC, M4A, MP3, WAV, OGG ou FLAC · 500 Mo maximum"}</small>
          </label>
          <div className="upload-options">
            <label>
              Qualité de transcription
              <select value={model} onChange={(event) => setModel(event.target.value)}>
                <option value="tiny">Rapide</option>
                <option value="base">Équilibrée — recommandé</option>
                <option value="small">Précise</option>
              </select>
            </label>
            <button className="button accent" disabled={!file || submitting} type="submit">
              {submitting ? "Envoi en cours…" : "Lancer le traitement"}
            </button>
          </div>
        </form>
      ) : (
        <div className="job-card">
          <div className="job-heading">
            <div>
              <span className={`job-state ${job.state}`}>{job.state}</span>
              <h2>{job.state === "completed" ? "Contenus prêts" : "Traitement en cours"}</h2>
              <p>{job.error || job.message}</p>
            </div>
            <strong className="progress-value">{progress}%</strong>
          </div>
          <div className="progress-track"><span style={{ width: `${progress}%` }} /></div>

          {job.state === "completed" && (
            <div className="result-summary">
              <div><strong>{job.metrics.segments ?? "—"}</strong><span>segments</span></div>
              <div><strong>{job.metrics.parts ?? "—"}</strong><span>parties</span></div>
              <div><strong>{formatDuration(job.metrics.duration_seconds)}</strong><span>audio</span></div>
              <div><strong>{formatDuration(job.metrics.elapsed_seconds)}</strong><span>traitement</span></div>
            </div>
          )}

          {job.artifacts.length > 0 && (
            <div className="artifact-grid">
              {job.artifacts.map((artifact) => (
                <a href={api.artifactUrl(job.id, artifact)} key={artifact} download>
                  <span>↓</span><strong>{artifactLabels[artifact] || artifact}</strong><small>Télécharger</small>
                </a>
              ))}
            </div>
          )}

          <div className="job-actions">
            {job.state === "running" && <button disabled={actionPending} onClick={() => runAction("pause")}>Mettre en pause</button>}
            {job.state === "paused" && <button disabled={actionPending} onClick={() => runAction("resume")}>Reprendre</button>}
            {!terminalStates.has(job.state) && <button className="danger" disabled={actionPending} onClick={() => runAction("cancel")}>Annuler</button>}
            {terminalStates.has(job.state) && <button disabled={actionPending} onClick={() => runAction("delete")}>Nouveau traitement</button>}
          </div>
        </div>
      )}
      {error && <p className="form-error notice">{error}</p>}
    </section>
  );
}

function Dashboard({ user, onLogout }: { user: User; onLogout: () => void }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState("");
  const [selectedProject, setSelectedProject] = useState<Project | null>(null);

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
          <a className={!selectedProject ? "active" : ""} href="#dashboard" onClick={() => setSelectedProject(null)}><span>⌂</span> Vue d’ensemble</a>
          <a href="#projects" onClick={() => setSelectedProject(null)}><span>▱</span> Mes projets</a>
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
        {selectedProject ? (
          <ProjectWorkspace project={selectedProject} onBack={() => setSelectedProject(null)} />
        ) : (
          <>
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
                <button aria-label={`Ouvrir ${project.title}`} onClick={() => setSelectedProject(project)}>→</button>
              </article>
            ))}
          </div>
        )}
          </>
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
