import { FormEvent, useEffect, useState } from "react";
import { api, BillingSummary, Job, Project, User } from "./api";

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

function formatCurrency(value: string, currency = "USD") {
  return new Intl.NumberFormat("fr-FR", {
    style: "currency",
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  }).format(Number(value));
}

function usageQuantity(quantity: number, unit: string) {
  if (unit === "audio_second") return formatDuration(quantity);
  if (unit === "micro_gb_month") return `${(quantity / 1_000_000).toFixed(6)} Go-mois`;
  return `${quantity.toLocaleString("fr-FR")} ${unit}`;
}

function ProjectEditor({
  project,
  onClose,
  onSaved,
  onDeleted,
}: {
  project: Project;
  onClose: () => void;
  onSaved: (project: Project) => void;
  onDeleted: (projectId: string) => void;
}) {
  const [title, setTitle] = useState(project.title);
  const [description, setDescription] = useState(project.description);
  const [saving, setSaving] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [error, setError] = useState("");

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!title.trim()) return;
    setSaving(true);
    setError("");
    try {
      onSaved(await api.updateProject(project.id, title.trim(), description.trim()));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Modification impossible.");
    } finally {
      setSaving(false);
    }
  }

  async function remove() {
    setSaving(true);
    setError("");
    try {
      await api.deleteProject(project.id);
      onDeleted(project.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Suppression impossible.");
      setConfirmingDelete(false);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section aria-labelledby="project-editor-title" aria-modal="true" className="project-editor" role="dialog">
        <header>
          <div><span className="eyebrow">Projet</span><h2 id="project-editor-title">Modifier le projet</h2></div>
          <button aria-label="Fermer" className="icon-button" onClick={onClose}>×</button>
        </header>
        <form onSubmit={save}>
          <label>
            Titre
            <input maxLength={180} onChange={(event) => setTitle(event.target.value)} required value={title} />
          </label>
          <label>
            Description
            <textarea maxLength={4000} onChange={(event) => setDescription(event.target.value)} rows={5} value={description} />
          </label>
          {error && <p className="form-error notice">{error}</p>}
          <div className="modal-actions">
            <button className="button secondary" onClick={onClose} type="button">Annuler</button>
            <button className="button primary compact" disabled={saving || !title.trim()} type="submit">
              {saving ? "Enregistrement…" : "Enregistrer"}
            </button>
          </div>
        </form>
        <div className="danger-zone">
          <div><strong>Supprimer ce projet</strong><p>Les résultats temporaires associés seront également supprimés.</p></div>
          {confirmingDelete ? (
            <div className="delete-confirmation">
              <span>Confirmer la suppression ?</span>
              <button disabled={saving} onClick={() => setConfirmingDelete(false)}>Non</button>
              <button className="danger" disabled={saving} onClick={remove}>Oui, supprimer</button>
            </div>
          ) : (
            <button className="danger-outline" onClick={() => setConfirmingDelete(true)}>Supprimer</button>
          )}
        </div>
      </section>
    </div>
  );
}

function JobsOverview({ projects, onOpen }: { projects: Project[]; onOpen: (project: Project) => void }) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    const refresh = () => api.jobs()
      .then((items) => active && setJobs(items))
      .catch((reason) => active && setError(reason instanceof Error ? reason.message : "Chargement impossible."))
      .finally(() => active && setLoading(false));
    refresh();
    const timer = window.setInterval(refresh, 1500);
    return () => { active = false; window.clearInterval(timer); };
  }, []);

  return (
    <section className="jobs-overview">
      <header className="workspace-header">
        <div><span className="eyebrow">Suivi</span><h1>Traitements</h1><p>Retrouvez vos traitements même après avoir changé de page.</p></div>
      </header>
      {error && <p className="form-error notice">{error}</p>}
      {loading ? (
        <div className="empty-state"><span className="loader" /><p>Chargement des traitements…</p></div>
      ) : jobs.length === 0 ? (
        <div className="empty-state"><span className="empty-icon">↻</span><h3>Aucun traitement</h3><p>Lancez un traitement depuis l’un de vos projets.</p></div>
      ) : (
        <div className="jobs-list">
          {jobs.map((job) => {
            const project = projects.find((item) => item.id === job.project_id);
            const progress = Math.round(Math.max(0, Math.min(1, job.progress)) * 100);
            return (
              <article className="job-list-card" key={job.id}>
                <div><span className={`job-state ${job.state}`}>{job.state}</span><h3>{project?.title || "Projet indisponible"}</h3><p>{job.error || job.message}</p></div>
                <div className="job-list-progress"><strong>{progress}%</strong><div className="progress-track"><span style={{ width: `${progress}%` }} /></div></div>
                {project && <button className="button secondary" onClick={() => onOpen(project)}>Ouvrir</button>}
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}

function CostCards({ summary }: { summary: BillingSummary }) {
  const cards = [
    ["Coût confirmé", summary.confirmed_cost],
    ["Encore estimé", summary.estimated_cost],
    ["Paiements enregistrés", summary.paid],
    ["Solde", summary.balance],
  ];
  return (
    <div className="cost-cards">
      {cards.map(([label, value]) => (
        <article key={label}>
          <span>{label}</span>
          <strong>{formatCurrency(value, summary.currency)}</strong>
          <small>{summary.period}</small>
        </article>
      ))}
    </div>
  );
}

function BillingDetails({ summary }: { summary: BillingSummary }) {
  return (
    <>
      <section className="billing-section">
        <div className="section-heading">
          <div><span className="eyebrow">Consommation</span><h2>Détail des opérations</h2></div>
          <span>{summary.usage.length} écriture{summary.usage.length > 1 ? "s" : ""}</span>
        </div>
        {summary.usage.length === 0 ? (
          <div className="empty-state compact-empty"><span className="empty-icon">◎</span><h3>Aucun coût ce mois-ci</h3><p>Les estimations et coûts cloud apparaîtront ici.</p></div>
        ) : (
          <div className="billing-table-wrap">
            <table className="billing-table">
              <thead><tr><th>Projet</th><th>Service</th><th>Consommation</th><th>Statut</th><th>Montant</th></tr></thead>
              <tbody>{summary.usage.map((item) => (
                <tr key={item.id}>
                  <td><strong>{item.project_title || "Infrastructure"}</strong><small>{new Intl.DateTimeFormat("fr", { dateStyle: "medium" }).format(new Date(item.occurred_at))}</small></td>
                  <td>{item.service}<small>{item.model}</small></td>
                  <td>{usageQuantity(item.quantity, item.unit)}</td>
                  <td><span className={`cost-status ${item.status}`}>{item.status === "confirmed" ? "Confirmé" : "Estimé"}</span></td>
                  <td><strong>{formatCurrency(item.amount, item.currency)}</strong></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </section>
      {summary.payments.length > 0 && (
        <section className="billing-section">
          <div className="section-heading"><div><span className="eyebrow">Règlements</span><h2>Paiements enregistrés</h2></div></div>
          <div className="payment-list">{summary.payments.map((payment) => (
            <article key={payment.id}>
              <div><strong>{payment.reference || "Paiement manuel"}</strong><small>{new Intl.DateTimeFormat("fr", { dateStyle: "long" }).format(new Date(payment.paid_at))}</small></div>
              <strong>{formatCurrency(payment.amount, payment.currency)}</strong>
            </article>
          ))}</div>
        </section>
      )}
    </>
  );
}

function BillingOverview() {
  const [month, setMonth] = useState(new Date().toISOString().slice(0, 7));
  const [summary, setSummary] = useState<BillingSummary | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    setSummary(null);
    setError("");
    api.billingSummary(month).then(setSummary).catch((reason) => setError(reason instanceof Error ? reason.message : "Chargement impossible."));
  }, [month]);

  return (
    <section className="billing-overview">
      <header className="workspace-header">
        <div><span className="eyebrow">Transparence</span><h1>Coûts cloud</h1><p>Suivez chaque dépense associée à vos productions.</p></div>
        <label className="month-picker">Période<input type="month" value={month} onChange={(event) => setMonth(event.target.value)} /></label>
      </header>
      {error && <p className="form-error notice">{error}</p>}
      {!summary ? <div className="empty-state"><span className="loader" /><p>Calcul du relevé…</p></div> : <><CostCards summary={summary} /><BillingDetails summary={summary} /></>}
    </section>
  );
}

function AdminBillingOverview() {
  const [month, setMonth] = useState(new Date().toISOString().slice(0, 7));
  const [summaries, setSummaries] = useState<BillingSummary[]>([]);
  const [selectedUser, setSelectedUser] = useState("");
  const [amount, setAmount] = useState("");
  const [reference, setReference] = useState("");
  const [note, setNote] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function refresh() {
    setLoading(true);
    setError("");
    try {
      const items = await api.clientBillingSummaries(month);
      setSummaries(items);
      setSelectedUser((current) => current || items[0]?.user.id || "");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Chargement impossible.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { refresh(); }, [month]);

  async function recordPayment(event: FormEvent) {
    event.preventDefault();
    if (!selectedUser || !amount) return;
    setSaving(true);
    setError("");
    try {
      await api.recordManualPayment(selectedUser, amount, month, reference, note);
      setAmount("");
      setReference("");
      setNote("");
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Enregistrement impossible.");
    } finally {
      setSaving(false);
    }
  }

  const selected = summaries.find((item) => item.user.id === selectedUser);
  return (
    <section className="billing-overview">
      <header className="workspace-header">
        <div><span className="eyebrow">Administration</span><h1>Relevés clients</h1><p>Contrôlez la consommation et enregistrez les règlements reçus.</p></div>
        <label className="month-picker">Période<input type="month" value={month} onChange={(event) => setMonth(event.target.value)} /></label>
      </header>
      {error && <p className="form-error notice">{error}</p>}
      {loading ? <div className="empty-state"><span className="loader" /><p>Chargement des comptes…</p></div> : (
        <div className="admin-billing-grid">
          <section className="client-balances">
            <h2>Clients</h2>
            {summaries.length === 0 ? <p>Aucun compte client.</p> : summaries.map((item) => (
              <button className={selectedUser === item.user.id ? "active" : ""} key={item.user.id} onClick={() => setSelectedUser(item.user.id)}>
                <span><strong>{item.user.display_name}</strong><small>{item.user.email}</small></span>
                <strong>{formatCurrency(item.balance, item.currency)}</strong>
              </button>
            ))}
          </section>
          <section className="manual-payment-card">
            <span className="eyebrow">Paiement manuel</span>
            <h2>{selected?.user.display_name || "Sélectionnez un client"}</h2>
            {selected && <CostCards summary={selected} />}
            <form onSubmit={recordPayment}>
              <label>Montant<input min="0.000001" step="0.000001" type="number" value={amount} onChange={(event) => setAmount(event.target.value)} required /></label>
              <label>Référence<input value={reference} onChange={(event) => setReference(event.target.value)} placeholder="Virement, reçu…" /></label>
              <label>Note<textarea rows={3} value={note} onChange={(event) => setNote(event.target.value)} /></label>
              <button className="button primary compact" disabled={saving || !selectedUser} type="submit">{saving ? "Enregistrement…" : "Enregistrer le paiement"}</button>
            </form>
          </section>
        </div>
      )}
    </section>
  );
}

function ProjectWorkspace({ project, onBack, onEdit }: { project: Project; onBack: () => void; onEdit: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [model, setModel] = useState("base");
  const [job, setJob] = useState<Job | null | undefined>(undefined);
  const [submitting, setSubmitting] = useState(false);
  const [uploadStage, setUploadStage] = useState<"reserve" | "upload" | "validate" | "start" | null>(null);
  const [actionPending, setActionPending] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    setJob(undefined);
    api.jobs(project.id)
      .then((jobs) => active && setJob(jobs[0] ?? null))
      .catch((reason) => {
        if (active) {
          setJob(null);
          setError(reason instanceof Error ? reason.message : "Récupération du traitement impossible.");
        }
      });
    return () => { active = false; };
  }, [project.id]);

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
      setJob(await api.createJob(project.id, file, model, "fr", setUploadStage));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Import impossible.");
    } finally {
      setSubmitting(false);
      setUploadStage(null);
    }
  }

  async function runAction(action: "pause" | "resume" | "cancel" | "delete" | "delete-source") {
    if (!job) return;
    setActionPending(true);
    setError("");
    try {
      if (action === "delete") {
        await api.deleteJob(job.id);
        setJob(null);
        setFile(null);
      } else if (action === "delete-source") {
        setJob(await api.deleteJobSource(job.id));
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
        <div className="project-header-actions">
          <button className="button secondary" onClick={onEdit}>Modifier</button>
          <span className="privacy-badge">Traitement temporaire · suppression automatique</span>
        </div>
      </header>

      {job === undefined ? (
        <div className="empty-state"><span className="loader" /><p>Recherche des traitements du projet…</p></div>
      ) : !job ? (
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
              {submitting ? ({
                reserve: "Préparation…",
                upload: "Envoi temporaire…",
                validate: "Vérification…",
                start: "Démarrage…",
              }[uploadStage || "reserve"]) : "Lancer le traitement"}
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
          {job.source_expires_at && (
            <p className="retention-note">
              Média source conservé temporairement jusqu’au {new Intl.DateTimeFormat("fr", {
                dateStyle: "long",
                timeStyle: "short",
              }).format(new Date(job.source_expires_at))}.
            </p>
          )}

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
            {terminalStates.has(job.state) && job.source_asset_id && (
              <button className="danger" disabled={actionPending} onClick={() => runAction("delete-source")}>Supprimer le média source</button>
            )}
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
  const [editingProject, setEditingProject] = useState<Project | null>(null);
  const [view, setView] = useState<"dashboard" | "jobs" | "billing" | "admin-billing">("dashboard");

  useEffect(() => {
    api.projects()
      .then(setProjects)
      .catch((reason) => setError(reason.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (loading) return;
    const restoreLocation = () => {
      const projectId = window.location.hash.match(/^#project-([a-f0-9]{32})$/)?.[1];
      if (projectId) {
        const project = projects.find((item) => item.id === projectId);
        if (project) {
          setSelectedProject(project);
          setView("dashboard");
        }
        return;
      }
      setSelectedProject(null);
      const hashView = {
        "#jobs": "jobs",
        "#billing": "billing",
        "#admin-billing": "admin-billing",
      }[window.location.hash] as typeof view | undefined;
      setView(hashView || "dashboard");
    };
    restoreLocation();
    window.addEventListener("hashchange", restoreLocation);
    return () => window.removeEventListener("hashchange", restoreLocation);
  }, [loading, projects]);

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

  function openProject(project: Project) {
    setSelectedProject(project);
    setView("dashboard");
    window.location.hash = `project-${project.id}`;
  }

  function showDashboard(anchor = "dashboard") {
    setSelectedProject(null);
    setView("dashboard");
    window.location.hash = anchor;
  }

  function projectSaved(project: Project) {
    setProjects((current) => current.map((item) => item.id === project.id ? project : item));
    setSelectedProject((current) => current?.id === project.id ? project : current);
    setEditingProject(null);
  }

  function projectDeleted(projectId: string) {
    setProjects((current) => current.filter((project) => project.id !== projectId));
    setSelectedProject((current) => current?.id === projectId ? null : current);
    setEditingProject(null);
    showDashboard("projects");
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Brand />
        <nav>
          <a className={!selectedProject && view === "dashboard" ? "active" : ""} href="#dashboard" onClick={() => showDashboard()}><span>⌂</span> Vue d’ensemble</a>
          <a href="#projects" onClick={() => showDashboard("projects")}><span>▱</span> Mes projets</a>
          <a href="#tools"><span>◇</span> Outils</a>
          <a className={view === "jobs" ? "active" : ""} href="#jobs" onClick={() => { setSelectedProject(null); setView("jobs"); }}><span>↻</span> Traitements</a>
          <a className={view === "billing" ? "active" : ""} href="#billing" onClick={() => { setSelectedProject(null); setView("billing"); }}><span>◉</span> Coûts</a>
          {user.role === "admin" && <a className={view === "admin-billing" ? "active" : ""} href="#admin-billing" onClick={() => { setSelectedProject(null); setView("admin-billing"); }}><span>▤</span> Administration</a>}
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
          <ProjectWorkspace project={selectedProject} onBack={() => showDashboard("projects")} onEdit={() => setEditingProject(selectedProject)} />
        ) : view === "jobs" ? (
          <JobsOverview projects={projects} onOpen={openProject} />
        ) : view === "billing" ? (
          <BillingOverview />
        ) : view === "admin-billing" && user.role === "admin" ? (
          <AdminBillingOverview />
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
                <div className="project-card-actions">
                  <button aria-label={`Modifier ${project.title}`} onClick={() => setEditingProject(project)}>✎</button>
                  <button aria-label={`Ouvrir ${project.title}`} onClick={() => openProject(project)}>→</button>
                </div>
              </article>
            ))}
          </div>
        )}
          </>
        )}
      </main>
      {editingProject && (
        <ProjectEditor
          onClose={() => setEditingProject(null)}
          onDeleted={projectDeleted}
          onSaved={projectSaved}
          project={editingProject}
        />
      )}
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
