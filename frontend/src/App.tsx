import { FormEvent, useEffect, useState } from "react";
import { api, AnalysisSegment, BillingSummary, CommunityAllocation, CommunityContribution, ImpactSummary, InvitationDetails, Job, JobAnalysis, Project, ProviderInvoice, TranscriptionQuote, User } from "./api";
import { TemplateLibrary } from "./TemplateLibrary";
import type { VisualMode } from "./TemplateLibrary";
import { UserAdministration } from "./UserAdministration";
import { PasswordField } from "./PasswordField";
import type { BrandTemplate } from "./api";

function inspectAudioDuration(file: File): Promise<number> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const audio = document.createElement("audio");
    const cleanup = () => URL.revokeObjectURL(url);
    audio.preload = "metadata";
    audio.onloadedmetadata = () => {
      const duration = audio.duration;
      cleanup();
      if (Number.isFinite(duration) && duration > 0) resolve(duration);
      else reject(new Error("La durée du fichier audio est illisible."));
    };
    audio.onerror = () => {
      cleanup();
      reject(new Error("Le navigateur ne peut pas analyser ce fichier audio."));
    };
    audio.src = url;
  });
}

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
          <PasswordField autoComplete="current-password" label="Mot de passe" onChange={setPassword} value={password} />
          <a className="forgot-link" href="#forgot-password">Mot de passe oublié ?</a>
          {error && <p className="form-error">{error}</p>}
          <button className="button primary" disabled={loading} type="submit">
            {loading ? "Connexion…" : "Se connecter"}
          </button>
        </form>
      </section>
    </main>
  );
}

function invitationTokenFromHash() {
  const match = window.location.hash.match(/^#invitation=([^&]+)$/);
  if (!match) return null;
  try {
    return decodeURIComponent(match[1]);
  } catch {
    return null;
  }
}

function resetTokenFromHash() {
  const match = window.location.hash.match(/^#reset-password=([^&]+)$/);
  if (!match) return null;
  try { return decodeURIComponent(match[1]); } catch { return null; }
}

function PasswordRecovery({ token }: { token: string | null }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [saving, setSaving] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    if (token && password !== confirmation) {
      setError("Les deux mots de passe ne correspondent pas.");
      return;
    }
    setSaving(true);
    try {
      if (token) await api.confirmPasswordReset(token, password);
      else await api.requestPasswordReset(email);
      setDone(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Opération impossible.");
    } finally { setSaving(false); }
  }

  return <main className="invitation-shell"><Brand /><section className="invitation-card">
    <span className="eyebrow">Accès sécurisé</span>
    <h1>{token ? "Nouveau mot de passe" : "Mot de passe oublié"}</h1>
    {done ? <p>{token ? "Votre mot de passe a été changé. Toutes les autres sessions ont été fermées." : "Si cette adresse correspond à un compte actif, un lien de réinitialisation vient d’être envoyé. Vérifiez aussi vos courriers indésirables."}</p> : <>
      <p>{token ? "Choisissez votre nouveau mot de passe." : "Indiquez l’adresse e-mail de votre compte pour recevoir un lien valable une heure."}</p>
      <form onSubmit={submit}>
        {token ? <><PasswordField label="Nouveau mot de passe" minLength={10} onChange={setPassword} value={password} /><PasswordField label="Confirmation" minLength={10} onChange={setConfirmation} value={confirmation} /></> : <label>Adresse e-mail<input autoComplete="email" required type="email" value={email} onChange={(event) => setEmail(event.target.value)} /></label>}
        {error && <p className="form-error notice">{error}</p>}
        <button className="button primary" disabled={saving} type="submit">{saving ? "Envoi…" : token ? "Changer le mot de passe" : "Recevoir le lien"}</button>
      </form>
    </>}
    <a className="button secondary invitation-login" href="/">Retour à la connexion</a>
  </section></main>;
}

function InvitationAcceptance({ token }: { token: string }) {
  const [details, setDetails] = useState<InvitationDetails | null>(null);
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [accepted, setAccepted] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api.invitationDetails(token)
      .then(setDetails)
      .catch((reason) => setError(reason instanceof Error ? reason.message : "Invitation invalide."))
      .finally(() => setLoading(false));
  }, [token]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    if (password !== confirmation) {
      setError("Les deux mots de passe ne correspondent pas.");
      return;
    }
    setSaving(true);
    try {
      await api.acceptInvitation(token, displayName, password);
      setAccepted(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Activation impossible.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <main className="invitation-shell">
      <Brand />
      <section className="invitation-card">
        {loading ? <div className="invitation-loading"><span className="loader" /><p>Vérification de l’invitation…</p></div> : accepted ? <>
          <span className="invitation-success">✓</span>
          <span className="eyebrow">Compte activé</span>
          <h1>Bienvenue sur Dars Manager</h1>
          <p>Votre adresse e-mail est vérifiée et votre mot de passe a été enregistré.</p>
          <a className="button primary invitation-login" href="/">Se connecter</a>
        </> : details ? <>
          <span className="eyebrow">Invitation sécurisée</span>
          <h1>Activez votre compte</h1>
          <p>Confirmez l’accès à <strong>{details.email}</strong> en choisissant le nom qui sera affiché dans Dars Manager et votre mot de passe.</p>
          <form onSubmit={submit}>
            <label>Nom affiché<input autoComplete="name" maxLength={120} required value={displayName} onChange={(event) => setDisplayName(event.target.value)} /></label>
            <PasswordField label="Mot de passe" minLength={10} onChange={setPassword} value={password} />
            <PasswordField label="Confirmation" minLength={10} onChange={setConfirmation} value={confirmation} />
            {error && <p className="form-error notice">{error}</p>}
            <button className="button primary" disabled={saving} type="submit">{saving ? "Activation…" : "Activer mon compte"}</button>
          </form>
          <small>Ce lien expire le {new Intl.DateTimeFormat("fr", { dateStyle: "long", timeStyle: "short" }).format(new Date(details.expires_at))}.</small>
        </> : <>
          <span className="invitation-error">!</span>
          <span className="eyebrow">Invitation indisponible</span>
          <h1>Ce lien n’est plus valide</h1>
          <p>{error || "Demandez à l’administrateur de vous envoyer une nouvelle invitation."}</p>
          <a className="button secondary invitation-login" href="/">Retour à la connexion</a>
        </>}
      </section>
    </main>
  );
}

const terminalStates = new Set(["completed", "cancelled", "failed", "expired"]);
function formatDuration(seconds?: number) {
  if (seconds === undefined) return "—";
  const rounded = Math.round(seconds);
  const minutes = Math.floor(rounded / 60);
  return `${minutes}:${String(rounded % 60).padStart(2, "0")}`;
}

function formatBytes(bytes: number) {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 1024 ? 1 : 0)} Ko`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} Mo`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} Go`;
}

function formatEditorTime(seconds: number) {
  const rounded = Math.round(seconds * 10) / 10;
  const hours = Math.floor(rounded / 3600);
  const minutes = Math.floor((rounded % 3600) / 60);
  const remaining = (rounded % 60).toFixed(Number.isInteger(rounded) ? 0 : 1).padStart(2, "0");
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, "0")}:${remaining.padStart(2, "0")}`
    : `${minutes}:${remaining.padStart(2, "0")}`;
}

function parseEditorTime(value: string) {
  const pieces = value.trim().split(":");
  if (pieces.length < 1 || pieces.length > 3 || pieces.some((piece) => piece === "" || !/^\d+(\.\d+)?$/.test(piece))) {
    return Number.NaN;
  }
  const values = pieces.map(Number);
  if (pieces.length === 3) return values[0] * 3600 + values[1] * 60 + values[2];
  if (pieces.length === 2) return values[0] * 60 + values[1];
  return values[0];
}

type PartDraft = {
  index: number;
  start: string;
  end: string;
  title: string;
  description: string;
  transcript: string;
};

type StudioLab = "audio-creation" | "subtitles" | "audio-adjustment" | "video-creation" | "distribution";
const pageOpenedAt = Date.now() / 1000;

function analysisDrafts(analysis: JobAnalysis): PartDraft[] {
  return analysis.parts.map((part) => ({
    ...part,
    start: formatEditorTime(part.start),
    end: formatEditorTime(part.end),
  }));
}

type ScopedSubtitleCue = AnalysisSegment & {
  sourceIndex: number;
};

function subtitleCuesForAudio(
  subtitles: JobAnalysis["subtitles"],
  ranges: [number, number][],
): ScopedSubtitleCue[] {
  const result: ScopedSubtitleCue[] = [];
  let offset = 0;
  for (const [rangeStart, rangeEnd] of ranges) {
    subtitles.cues.forEach((cue, sourceIndex) => {
      if (cue.end <= rangeStart || cue.start >= rangeEnd) return;
      result.push({
        sourceIndex,
        start: offset + Math.max(rangeStart, cue.start) - rangeStart,
        end: offset + Math.min(rangeEnd, cue.end) - rangeStart,
        text: cue.text,
      });
    });
    offset += rangeEnd - rangeStart;
  }
  return result;
}

function subtitleSourceForAudio(
  subtitles: JobAnalysis["subtitles"],
  ranges: [number, number][],
): JobAnalysis["subtitles"] {
  return {
    ...subtitles,
    cues: subtitles.cues.filter((cue) => ranges.some(
      ([rangeStart, rangeEnd]) => cue.end > rangeStart && cue.start < rangeEnd,
    )),
  };
}

function CourseEditor({ job }: { job: Job }) {
  const [returningToCourse] = useState(() => window.sessionStorage.getItem(`dars-course-visited:${job.id}`) === "1");
  const [activeLab, setActiveLab] = useState<StudioLab>("audio-creation");
  const [analysis, setAnalysis] = useState<JobAnalysis | null>(null);
  const [subtitleDraft, setSubtitleDraft] = useState<JobAnalysis["subtitles"] | null>(null);
  const [subtitleTrackName, setSubtitleTrackName] = useState("");
  const [selectedSubtitleTrackId, setSelectedSubtitleTrackId] = useState<string | null>(null);
  const [selectedVideoSubtitleTrackId, setSelectedVideoSubtitleTrackId] = useState("");
  const [subtitleDirty, setSubtitleDirty] = useState(false);
  const [subtitleSaving, setSubtitleSaving] = useState(false);
  const [proofreadJob, setProofreadJob] = useState<Job | null>(null);
  const [includeSubtitles, setIncludeSubtitles] = useState(false);
  const [parts, setParts] = useState<PartDraft[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [selectedParts, setSelectedParts] = useState<number[]>([]);
  const [exportJob, setExportJob] = useState<Job | null>(null);
  const [audioExports, setAudioExports] = useState<Job[]>([]);
  const [videoJob, setVideoJob] = useState<Job | null>(null);
  const [archiveJob, setArchiveJob] = useState<Job | null>(null);
  const [recoveryArchive, setRecoveryArchive] = useState<Job | null>(null);
  const [reanalysisJob, setReanalysisJob] = useState<Job | null>(null);
  const [exportLoading, setExportLoading] = useState(true);
  const [exporting, setExporting] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [selectedTemplate, setSelectedTemplate] = useState<BrandTemplate | null>(null);
  const [visualMode, setVisualMode] = useState<VisualMode>("ready");
  const [imagePrompt, setImagePrompt] = useState("");
  const [imageJob, setImageJob] = useState<Job | null>(null);
  const [generatingImage, setGeneratingImage] = useState(false);
  const [videoFormat, setVideoFormat] = useState<"16:9" | "1:1" | "9:16">("16:9");
  const [videoValues, setVideoValues] = useState({ title: "", speaker: "", date: "", episode: "" });
  const [renderingVideo, setRenderingVideo] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const selectedAudio = exportJob?.state === "completed" ? exportJob : null;

  useEffect(() => { window.sessionStorage.setItem(`dars-course-visited:${job.id}`, "1"); }, [job.id]);

  function load() {
    setLoading(true);
    setError("");
    setNotice("");
    return api.jobAnalysis(job.id)
      .then((value) => {
        setAnalysis(value);
        setParts(analysisDrafts(value));
        setSelectedParts((current) => current.length ? current : value.parts.map((part) => part.index));
        setDirty(false);
      })
      .catch((reason) => setError(reason instanceof Error ? reason.message : "Analyse indisponible."))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    let active = true;
    setLoading(true);
    api.jobAnalysis(job.id)
      .then((value) => {
        if (!active) return;
        setAnalysis(value);
        setParts(analysisDrafts(value));
        setSelectedParts((current) => current.length ? current : value.parts.map((part) => part.index));
        setDirty(false);
      })
      .catch((reason) => {
        if (active) setError(reason instanceof Error ? reason.message : "Analyse indisponible.");
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [job.id]);

  useEffect(() => {
    let active = true;
    setExportLoading(true);
    api.jobs(job.project_id)
      .then((jobs) => {
        if (!active) return;
        const savedAudio = jobs.filter((item) => item.tool === "audio_selection" && item.parent_job_id === job.id);
        const latestAudio = savedAudio[0] ?? null;
        setAudioExports(savedAudio);
        setExportJob(latestAudio);
        if (latestAudio?.content?.part_indices.length) {
          setSelectedParts(latestAudio.content.part_indices);
        }
        setVideoJob(jobs.find((item) => item.tool === "video_render" && item.parent_job_id === job.id) ?? null);
        setImageJob(jobs.find((item) => item.tool === "image_generation" && item.parent_job_id === job.id) ?? null);
        setArchiveJob(jobs.find((item) => item.tool === "archive_export" && item.parent_job_id === job.id) ?? null);
        setReanalysisJob(jobs.find((item) => (
          item.tool === "semantic_reanalysis"
          && item.parent_job_id === job.id
          && !terminalStates.has(item.state)
        )) ?? null);
        setProofreadJob(jobs.find((item) => item.tool === "subtitle_proofread" && item.parent_job_id === job.id && !terminalStates.has(item.state)) ?? null);
      })
      .catch(() => { if (active) setExportJob(null); })
      .finally(() => { if (active) setExportLoading(false); });
    return () => { active = false; };
  }, [job.id, job.project_id]);

  useEffect(() => {
    if (!analysis || !selectedAudio || subtitleDirty) return;
    const tracks = (analysis.subtitle_tracks || []).filter(
      (track) => track.audio_export_job_id === selectedAudio.id,
    );
    const selected = tracks.find((track) => track.id === selectedSubtitleTrackId) || tracks[0];
    if (selected) {
      setSelectedSubtitleTrackId(selected.id);
      setSubtitleTrackName(selected.name);
      setSubtitleDraft({
        language: selected.language,
        font: selected.font,
        color: selected.color,
        cues: selected.cues,
      });
      return;
    }
    setSelectedSubtitleTrackId(null);
    setSubtitleTrackName(`Sous-titres ${analysis.subtitles.language.toUpperCase()}`);
    setSubtitleDraft(subtitleSourceForAudio(
      analysis.subtitles,
      selectedAudio.content?.ranges || [],
    ));
  }, [analysis?.checksum_sha256, selectedAudio?.id]);

  useEffect(() => {
    if (!analysis || !selectedAudio) return;
    const tracks = (analysis.subtitle_tracks || []).filter(
      (track) => track.audio_export_job_id === selectedAudio.id,
    );
    if (!tracks.some((track) => track.id === selectedVideoSubtitleTrackId)) {
      setSelectedVideoSubtitleTrackId(
        tracks.find((track) => track.id === selectedSubtitleTrackId)?.id || tracks[0]?.id || "",
      );
      if (!tracks.length) setIncludeSubtitles(false);
    }
  }, [analysis?.checksum_sha256, selectedAudio?.id, selectedSubtitleTrackId]);

  useEffect(() => {
    function checkRecovery() {
      api.recoveryArchive(job.id).then((latest) => {
        if (latest && (returningToCourse || latest.created_at < pageOpenedAt) && window.localStorage.getItem(`dars-archive-seen:${job.id}`) !== latest.id && window.sessionStorage.getItem(`dars-archive-deferred:${job.id}`) !== latest.id) setRecoveryArchive(latest);
      }).catch(() => {});
    }
    checkRecovery();
    const timer = window.setInterval(checkRecovery, 15000);
    return () => window.clearInterval(timer);
  }, [job.id, job.project_id, returningToCourse]);

  useEffect(() => {
    if (!exportJob || terminalStates.has(exportJob.state)) return;
    const timer = window.setInterval(() => {
      api.job(exportJob.id)
        .then((updated) => {
          setExportJob(updated);
          setAudioExports((current) => {
            const found = current.some((item) => item.id === updated.id);
            return found
              ? current.map((item) => item.id === updated.id ? updated : item)
              : [updated, ...current];
          });
        })
        .catch((reason) => setError(reason instanceof Error ? reason.message : "Suivi de l’export impossible."));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [exportJob?.id, exportJob?.state]);

  useEffect(() => {
    if (!imageJob || terminalStates.has(imageJob.state)) return;
    const timer = window.setInterval(() => {
      api.job(imageJob.id)
        .then(setImageJob)
        .catch((reason) => setError(reason instanceof Error ? reason.message : "Suivi de l’image impossible."));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [imageJob?.id, imageJob?.state]);

  useEffect(() => {
    if (!videoJob || terminalStates.has(videoJob.state)) return;
    const timer = window.setInterval(() => {
      api.job(videoJob.id)
        .then(setVideoJob)
        .catch((reason) => setError(reason instanceof Error ? reason.message : "Suivi du rendu impossible."));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [videoJob?.id, videoJob?.state]);

  useEffect(() => {
    if (!archiveJob || terminalStates.has(archiveJob.state)) return;
    const timer = window.setInterval(() => {
      api.job(archiveJob.id)
        .then(setArchiveJob)
        .catch((reason) => setError(reason instanceof Error ? reason.message : "Suivi de l'archive impossible."));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [archiveJob?.id, archiveJob?.state]);

  useEffect(() => {
    if (!reanalysisJob) return;
    if (reanalysisJob.state === "completed") {
      load().then(() => {
        setNotice("Les sous-chapitres et leurs titres ont été recréés à partir du contenu.");
        setReanalysisJob(null);
      });
      return;
    }
    if (terminalStates.has(reanalysisJob.state)) return;
    const timer = window.setInterval(() => {
      api.job(reanalysisJob.id)
        .then(setReanalysisJob)
        .catch((reason) => setError(reason instanceof Error ? reason.message : "Suivi de la réanalyse impossible."));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [reanalysisJob?.id, reanalysisJob?.state]);

  useEffect(() => {
    if (!proofreadJob) return;
    if (proofreadJob.state === "completed") {
      api.subtitleSuggestions(job.id, proofreadJob.id).then((result) => {
        if (result.analysis_checksum !== analysis?.checksum_sha256 || subtitleDirty) {
          setError("Le texte a changé depuis la correction. Enregistrez ou rechargez avant de relancer.");
        } else if (subtitleDraft && result.cues.length === subtitleDraft.cues.length) {
          setSubtitleDraft({ ...subtitleDraft, cues: result.cues });
          setSubtitleDirty(true);
          setNotice("Corrections proposées. Relisez-les puis enregistrez-les.");
        }
        setProofreadJob(null);
      }).catch((reason) => { setError(reason instanceof Error ? reason.message : "Suggestions indisponibles."); setProofreadJob(null); });
      return;
    }
    if (terminalStates.has(proofreadJob.state)) return;
    const timer = window.setInterval(() => api.job(proofreadJob.id).then(setProofreadJob).catch(() => {}), 1500);
    return () => window.clearInterval(timer);
  }, [proofreadJob?.id, proofreadJob?.state, analysis?.checksum_sha256, subtitleDirty]);

  function changePart(index: number, field: keyof PartDraft, value: string) {
    setParts((current) => current.map((part, position) => (
      position === index ? { ...part, [field]: value } : part
    )));
    setDirty(true);
    setNotice("");
  }

  async function save() {
    if (!analysis) return false;
    const parsed = parts.map((part) => ({
      index: part.index,
      start: parseEditorTime(part.start),
      end: parseEditorTime(part.end),
      title: part.title.trim(),
      description: part.description.trim(),
    }));
    if (parsed.some((part) => !Number.isFinite(part.start) || !Number.isFinite(part.end))) {
      setError("Utilisez le format minutes:secondes, par exemple 12:35.");
      return false;
    }
    if (parsed.some((part) => !part.title)) {
      setError("Chaque partie doit avoir un titre.");
      return false;
    }
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const updated = await api.updateJobAnalysis(job.id, analysis.checksum_sha256, parsed);
      setAnalysis(updated);
      setParts(analysisDrafts(updated));
      setDirty(false);
      setNotice("Corrections enregistrées sans nouvelle transcription ni coût IA.");
      return updated;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Sauvegarde impossible.");
      return false;
    } finally {
      setSaving(false);
    }
  }

  async function saveSubtitles(checksumOverride?: string) {
    if (!analysis || !subtitleDraft || !selectedAudio) return false;
    if (!subtitleTrackName.trim()) {
      setError("Donnez un nom à cette piste de sous-titres.");
      return false;
    }
    const trackId = selectedSubtitleTrackId || crypto.randomUUID().replaceAll("-", "");
    setSubtitleSaving(true);
    setError("");
    try {
      const updated = await api.updateSubtitles(
        job.id,
        checksumOverride || analysis.checksum_sha256,
        trackId,
        selectedAudio.id,
        subtitleTrackName.trim(),
        subtitleDraft,
      );
      setAnalysis(updated);
      setSelectedSubtitleTrackId(trackId);
      setSelectedVideoSubtitleTrackId(trackId);
      setSubtitleDirty(false);
      setNotice("Piste de sous-titres enregistrée sur le serveur.");
      return { analysis: updated, trackId };
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Enregistrement des sous-titres impossible.");
      return false;
    } finally {
      setSubtitleSaving(false);
    }
  }

  async function changeLab(next: StudioLab) {
    if (saving || subtitleSaving) return;
    const savedAnalysis = dirty ? await save() : analysis;
    if (!savedAnalysis) return;
    if (subtitleDirty && !(await saveSubtitles(savedAnalysis.checksum_sha256))) return;
    setActiveLab(next);
  }

  async function proofreadSubtitles() {
    if (!analysis || !exportJob || exportJob.state !== "completed") return;
    const saved = subtitleDirty || !selectedSubtitleTrackId
      ? await saveSubtitles()
      : { analysis, trackId: selectedSubtitleTrackId };
    if (!saved) return;
    setError("");
    try {
      const quote = await api.subtitleProofreadQuote(job.id, exportJob.id);
      if (!window.confirm(`Corriger l’orthographe et la grammaire avec ${quote.model} pour environ ${Number(quote.amount).toFixed(4)} ${quote.currency} ?`)) return;
      setProofreadJob(await api.createSubtitleProofread(
        job.id,
        saved.analysis.checksum_sha256,
        exportJob.id,
        saved.trackId,
      ));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Correction impossible."); }
  }

  async function reanalyze() {
    if (!analysis) return;
    setError("");
    setNotice("");
    try {
      const quote = await api.semanticReanalysisQuote(job.id);
      const replacementWarning = dirty
        ? " Vos modifications non enregistrées seront remplacées."
        : "";
      const confirmed = window.confirm(
        `Recréer les sous-chapitres et les titres avec ${quote.model} pour environ ${Number(quote.amount).toFixed(4)} ${quote.currency} ?${replacementWarning}`,
      );
      if (!confirmed) return;
      setReanalysisJob(await api.createSemanticReanalysis(
        job.id,
        analysis.checksum_sha256,
        true,
      ));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Réanalyse impossible.");
    }
  }

  function togglePart(index: number) {
    setSelectedParts((current) => current.includes(index)
      ? current.filter((item) => item !== index)
      : [...current, index]);
  }

  async function selectAudio(item: Job) {
    if (subtitleDirty && !(await saveSubtitles())) return;
    setExportJob(item);
    setSelectedParts(item.content?.part_indices || []);
    setSelectedSubtitleTrackId(null);
    setSubtitleDirty(false);
  }

  async function selectSubtitleTrack(trackId: string) {
    if (!analysis) return;
    let currentAnalysis = analysis;
    if (subtitleDirty) {
      const saved = await saveSubtitles();
      if (!saved) return;
      currentAnalysis = saved.analysis;
    }
    const track = (currentAnalysis.subtitle_tracks || []).find((item) => item.id === trackId);
    if (!track) return;
    setSelectedSubtitleTrackId(track.id);
    setSubtitleTrackName(track.name);
    setSubtitleDraft({
      language: track.language,
      font: track.font,
      color: track.color,
      cues: track.cues,
    });
    setSubtitleDirty(false);
  }

  async function createSubtitleTrack() {
    if (!analysis || !selectedAudio) return;
    let currentAnalysis = analysis;
    if (subtitleDirty) {
      const saved = await saveSubtitles();
      if (!saved) return;
      currentAnalysis = saved.analysis;
    }
    const existingCount = (currentAnalysis.subtitle_tracks || []).filter(
      (track) => track.audio_export_job_id === selectedAudio.id,
    ).length;
    setSelectedSubtitleTrackId(null);
    setSubtitleTrackName(`Sous-titres ${currentAnalysis.subtitles.language.toUpperCase()} ${existingCount + 1}`);
    setSubtitleDraft(subtitleSourceForAudio(
      currentAnalysis.subtitles,
      selectedAudio.content?.ranges || [],
    ));
    setSubtitleDirty(true);
    setNotice("Nouvelle piste prête à être personnalisée.");
  }

  async function createExport() {
    if (!analysis || selectedParts.length === 0) return;
    if (dirty) {
      setError("Enregistrez d’abord vos corrections avant de générer l’audio.");
      return;
    }
    setExporting(true);
    setError("");
    setNotice("");
    try {
      const created = await api.createAudioExport(job.id, analysis.checksum_sha256, selectedParts);
      setExportJob(created);
      setAudioExports((current) => [created, ...current.filter((item) => item.id !== created.id)]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Création de l’export impossible.");
    } finally {
      setExporting(false);
    }
  }

  async function createVideo() {
    if (!analysis || !selectedTemplate || selectedParts.length === 0 || !selectedAudio) return;
    if (dirty || (includeSubtitles && subtitleDirty)) {
      setError("Enregistrez d’abord vos corrections avant de générer la vidéo.");
      return;
    }
    setRenderingVideo(true);
    setError("");
    setNotice("");
    try {
      setVideoJob(await api.createVideoExport(
        job.id,
        analysis.checksum_sha256,
        selectedParts,
        selectedTemplate,
        videoFormat,
        videoValues,
        visualMode === "ai" ? imageJob?.id : undefined,
        includeSubtitles,
        selectedAudio.id,
        includeSubtitles ? selectedVideoSubtitleTrackId : undefined,
      ));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Création de la vidéo impossible.");
    } finally {
      setRenderingVideo(false);
    }
  }

  async function createImage() {
    if (!selectedTemplate) return;
    const title = videoValues.title.trim() || exportJob?.content?.title || analysis?.parts[0]?.title || "Cours audio";
    setGeneratingImage(true);
    setError("");
    setNotice("");
    try {
      const quote = await api.imageGenerationQuote(job.id);
      const confirmed = window.confirm(
        `Générer cette image avec ${quote.model} pour un coût estimé à ${Number(quote.amount).toFixed(4)} ${quote.currency} ?`,
      );
      if (!confirmed) return;
      setImageJob(await api.createImageGeneration(
        job.id,
        selectedTemplate,
        videoFormat,
        title,
        imagePrompt,
        true,
      ));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Génération de l’image impossible.");
    } finally {
      setGeneratingImage(false);
    }
  }

  async function createArchive() {
    if (!analysis) return;
    if (dirty) {
      setError("Enregistrez d'abord vos corrections avant de créer l'archive.");
      return;
    }
    setArchiving(true);
    setError("");
    setNotice("");
    try {
      setArchiveJob(await api.createArchiveExport(
        job.id,
        analysis.checksum_sha256,
        videoJob?.state === "completed" ? videoJob.id : undefined,
        exportJob?.state === "completed" ? exportJob.id : undefined,
      ));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Création de l'archive impossible.");
    } finally {
      setArchiving(false);
    }
  }

  const selectedDuration = parts
    .filter((part) => selectedParts.includes(part.index))
    .reduce((total, part) => {
      const start = parseEditorTime(part.start);
      const end = parseEditorTime(part.end);
      return total + (Number.isFinite(start) && Number.isFinite(end) ? Math.max(0, end - start) : 0);
    }, 0);
  const exportProgress = Math.round(Math.max(0, Math.min(1, exportJob?.progress ?? 0)) * 100);
  const exportBusy = Boolean(exportJob && !terminalStates.has(exportJob.state));
  const imageBusy = Boolean(imageJob && !terminalStates.has(imageJob.state));
  const imageProgress = Math.round(Math.max(0, Math.min(1, imageJob?.progress ?? 0)) * 100);
  const videoBusy = Boolean(videoJob && !terminalStates.has(videoJob.state));
  const videoProgress = Math.round(Math.max(0, Math.min(1, videoJob?.progress ?? 0)) * 100);
  const archiveBusy = Boolean(archiveJob && !terminalStates.has(archiveJob.state));
  const archiveProgress = Math.round(Math.max(0, Math.min(1, archiveJob?.progress ?? 0)) * 100);
  const reanalysisBusy = Boolean(reanalysisJob && !terminalStates.has(reanalysisJob.state));
  const proofreadBusy = Boolean(proofreadJob && !terminalStates.has(proofreadJob.state));
  const reanalysisProgress = Math.round(Math.max(0, Math.min(1, reanalysisJob?.progress ?? 0)) * 100);
  const audioReady = Boolean(analysis && job.artifacts.includes("audio"));
  const subtitleRanges = selectedAudio?.content?.ranges || [];
  const scopedSubtitleCues = subtitleDraft
    ? subtitleCuesForAudio(subtitleDraft, subtitleRanges)
    : [];
  const subtitleTracks = (analysis?.subtitle_tracks || []).filter(
    (track) => track.audio_export_job_id === selectedAudio?.id,
  );
  const selectedVideoSubtitleTrack = subtitleTracks.find(
    (track) => track.id === selectedVideoSubtitleTrackId,
  ) || null;
  const selectedVideoSubtitleCount = selectedVideoSubtitleTrack
    ? subtitleCuesForAudio(selectedVideoSubtitleTrack, subtitleRanges).length
    : 0;
  const effectiveVideoTitle = videoValues.title.trim() || exportJob?.content?.title || analysis?.parts[0]?.title || "Cours audio";
  const imageReady = Boolean(
    imageJob?.state === "completed"
    && imageJob.artifacts.includes("generated_image")
    && imageJob.content?.title === effectiveVideoTitle
    && imageJob.content?.template_id === selectedTemplate?.id
    && (imageJob.content?.prompt || "") === imagePrompt.trim()
    && imageJob.metrics.output_format === videoFormat
  );
  const visualReady = Boolean(selectedTemplate && (visualMode === "ready" || imageReady));
  const videoReady = Boolean(videoJob?.state === "completed" && videoJob.artifacts.includes("video"));
  const labs: { id: StudioLab; label: string; description: string; unlocked: boolean }[] = [
    { id: "audio-creation", label: "Création Audio", description: "Transcrire, chapitrer et sauvegarder les extraits", unlocked: true },
    { id: "subtitles", label: "Sous-titres", description: "Corriger et synchroniser le texte", unlocked: Boolean(selectedAudio) },
    { id: "audio-adjustment", label: "Ajustement Audio", description: "Étape optionnelle de montage et d’enrichissement", unlocked: Boolean(exportJob?.state === "completed") },
    { id: "video-creation", label: "Création Vidéo", description: "Composer le support visuel à partir d’un audio", unlocked: audioReady },
    { id: "distribution", label: "Diffusion", description: "Télécharger ou publier sur YouTube et Telegram", unlocked: videoReady },
  ];

  return (
    <section className="course-editor">
      {recoveryArchive && <div className="recovery-overlay" role="dialog" aria-modal="true" aria-label="Sauvegarde récupérable"><div className="recovery-card"><h2>Votre travail est récupérable</h2><p>Une sauvegarde temporaire .dars de ce cours est disponible sur le serveur. Téléchargez-la pour conserver une copie sur votre machine.</p><div><a className="button accent" download href={api.artifactUrl(recoveryArchive.id, "archive")} onClick={() => { window.localStorage.setItem(`dars-archive-seen:${job.id}`, recoveryArchive.id); setRecoveryArchive(null); }}>Télécharger la sauvegarde</a><button className="button secondary" onClick={() => { window.sessionStorage.setItem(`dars-archive-deferred:${job.id}`, recoveryArchive.id); setRecoveryArchive(null); }}>Plus tard</button></div></div></div>}
      <nav aria-label="Étapes du studio" className="studio-tabs" role="tablist">
        {labs.map((lab, index) => (
          <button
            aria-disabled={!lab.unlocked}
            aria-selected={activeLab === lab.id}
            className={`${activeLab === lab.id ? "active" : ""} ${lab.unlocked ? "" : "locked"}`}
            key={lab.id}
            onClick={() => lab.unlocked && void changeLab(lab.id)}
            role="tab"
            type="button"
          >
            <span>{lab.unlocked ? String(index + 1).padStart(2, "0") : "🔒"}</span>
            <strong>{lab.label}</strong>
            <small>{lab.description}</small>
          </button>
        ))}
      </nav>

      {error && (
        <div className="editor-feedback error">
          <span>{error}</span>
          <button onClick={load}>Recharger l’analyse</button>
        </div>
      )}
      {notice && <p className="editor-feedback success">{notice}</p>}

      {loading ? (
        <div className="editor-loading"><span className="loader" /><p>Ouverture de l’analyse…</p></div>
      ) : analysis && (
        <>
          {activeLab === "audio-creation" && (
            <section className="studio-lab-panel" role="tabpanel">
              <header className="editor-heading">
                <div>
                  <span className="eyebrow">Création Audio</span>
                  <h2>Créer des extraits structurés et réutilisables</h2>
                  <p>Relisez le chapitrage, corrigez les titres puis sauvegardez les parties utiles sous forme d’audios courts.</p>
                </div>
                <div className="project-header-actions">
                  <button className="button secondary compact" disabled={!analysis || reanalysisBusy} onClick={reanalyze}>
                    {reanalysisBusy ? "Analyse des sous-sujets…" : "Recréer les chapitres avec l’IA"}
                  </button>
                  <button className="button primary compact" disabled={saving || reanalysisBusy} onClick={save}>
                    {saving ? "Enregistrement…" : "Enregistrer les corrections"}
                  </button>
                </div>
              </header>

              {reanalysisJob && (
                <div className={`export-status ${reanalysisJob.state}`}>
                  <div><span className={`job-state ${reanalysisJob.state}`}>{reanalysisJob.state}</span><strong>Analyse des sous-sujets</strong><small>{reanalysisJob.error || reanalysisJob.message}</small></div>
                  {reanalysisBusy && <div className="export-progress"><strong>{reanalysisProgress}%</strong><div className="progress-track"><span style={{ width: `${reanalysisProgress}%` }} /></div></div>}
                </div>
              )}

              {job.artifacts.includes("audio") && (
                <div className="audio-review">
                  <span aria-hidden="true">▶</span>
                  <div><strong>{analysis.audio_name || "Audio du cours"}</strong><small>{formatDuration(analysis.duration_seconds)} · source de travail</small></div>
                  <audio controls preload="metadata" src={api.artifactUrl(job.id, "audio")} />
                </div>
              )}

              <div className="selection-toolbar">
                <div><span className="eyebrow">Sélection audio</span><strong>{selectedParts.length} partie{selectedParts.length > 1 ? "s" : ""} · {formatDuration(selectedDuration)}</strong></div>
                <div>
                  <button className="button secondary" onClick={() => setSelectedParts(selectedParts.length === parts.length ? [] : parts.map((part) => part.index))}>{selectedParts.length === parts.length ? "Tout désélectionner" : "Tout sélectionner"}</button>
                  <button className="button accent" disabled={selectedParts.length === 0 || exporting || dirty || exportBusy} onClick={createExport}>{exporting ? "Préparation…" : exportBusy ? "Sauvegarde en cours…" : "Sauvegarder l’audio sélectionné"}</button>
                </div>
              </div>

              {!exportLoading && exportJob && exportJob.state !== "completed" && (
                <div className={`export-status ${exportJob.state}`}>
                  <div><span className={`job-state ${exportJob.state}`}>{exportJob.state}</span><strong>Sauvegarde de l’extrait audio</strong><small>{exportJob.error || exportJob.message}</small></div>
                  {!terminalStates.has(exportJob.state) && <div className="export-progress"><strong>{exportProgress}%</strong><div className="progress-track"><span style={{ width: `${exportProgress}%` }} /></div></div>}
                </div>
              )}

              {audioExports.some((item) => item.state === "completed") && (
                <section className="saved-audio-library">
                  <header><div><span className="eyebrow">Audios sauvegardés</span><h3>Choisissez l’audio à utiliser dans les étapes suivantes</h3></div><span>{audioExports.filter((item) => item.state === "completed").length} audio(s)</span></header>
                  <div>
                    {audioExports.filter((item) => item.state === "completed").map((item) => (
                      <article className={exportJob?.id === item.id ? "selected" : ""} key={item.id}>
                        <button onClick={() => void selectAudio(item)} type="button">
                          <span>♪</span><strong>{item.content?.title || "Extrait audio"}</strong><small>{formatDuration(item.metrics.duration_seconds)}</small>
                        </button>
                        <audio controls preload="metadata" src={api.artifactUrl(item.id, "selection_audio")} />
                        <a className="button secondary" download href={api.artifactUrl(item.id, "selection_audio")}>Télécharger</a>
                      </article>
                    ))}
                  </div>
                  <footer>
                    <button className="button secondary" onClick={() => setActiveLab("audio-adjustment")}>Ajuster cet audio</button>
                    <button className="button accent" onClick={() => setActiveLab("video-creation")}>Créer directement la vidéo</button>
                  </footer>
                </section>
              )}

              <div className="course-parts">
                {parts.map((part, position) => (
                  <article className={`course-part ${selectedParts.includes(part.index) ? "selected" : ""}`} key={part.index}>
                    <div className="part-number"><label className="part-selector"><input checked={selectedParts.includes(part.index)} onChange={() => togglePart(part.index)} type="checkbox" /><span>Sélectionner</span></label><span>Partie</span><strong>{String(position + 1).padStart(2, "0")}</strong></div>
                    <div className="part-fields">
                      <label className="part-title">Titre<input maxLength={180} onChange={(event) => changePart(position, "title", event.target.value)} value={part.title} /></label>
                      <div className="time-fields"><label>Début<input aria-label={`Début de la partie ${position + 1}`} inputMode="decimal" onChange={(event) => changePart(position, "start", event.target.value)} value={part.start} /></label><span>→</span><label>Fin<input aria-label={`Fin de la partie ${position + 1}`} inputMode="decimal" onChange={(event) => changePart(position, "end", event.target.value)} value={part.end} /></label></div>
                      <label className="part-description">Description<textarea maxLength={4000} onChange={(event) => changePart(position, "description", event.target.value)} rows={3} value={part.description} /></label>
                      <details className="transcript-preview"><summary>Voir la transcription de cette partie</summary><p>{part.transcript || "Aucun texte dans cet intervalle."}</p></details>
                    </div>
                  </article>
                ))}
              </div>
            </section>
          )}

          {activeLab === "subtitles" && subtitleDraft && (
            <section className="studio-lab-panel subtitle-lab" role="tabpanel">
              <header className="editor-heading"><div><span className="eyebrow">Sous-titres · optionnels</span><h2>Préparer le texte affiché dans la vidéo</h2><p>Créez et conservez plusieurs pistes pour un même audio. Leur chronologie commence à 0:00 et correspond exactement à l’extrait.</p></div><div className="project-header-actions"><button className="button secondary compact" disabled={!selectedAudio || proofreadBusy || subtitleSaving} onClick={proofreadSubtitles}>{proofreadBusy ? "Correction en cours…" : "Corriger / traduire avec l’IA"}</button><button className="button primary compact" disabled={!subtitleDirty || subtitleSaving} onClick={() => void saveSubtitles()}>{subtitleSaving ? "Enregistrement…" : "Enregistrer cette piste"}</button></div></header>
              {proofreadJob && <p className="editor-feedback">{proofreadJob.error || proofreadJob.message}</p>}
              {selectedAudio && <div className="adjustment-source"><div><span>Audio sélectionné</span><strong>{selectedAudio.content?.title || "Extrait audio"}</strong><small>{scopedSubtitleCues.length} sous-titre{scopedSubtitleCues.length > 1 ? "s" : ""} · {formatDuration(selectedAudio.metrics.duration_seconds)}</small></div><audio controls preload="metadata" src={api.artifactUrl(selectedAudio.id, "selection_audio")} /></div>}
              <section className="subtitle-track-library">
                <header><div><span className="eyebrow">Pistes sauvegardées</span><h3>Versions disponibles pour cet audio</h3></div><button className="button secondary compact" disabled={proofreadBusy || subtitleSaving} onClick={() => void createSubtitleTrack()} type="button">＋ Nouvelle piste</button></header>
                {subtitleTracks.length ? <div>{subtitleTracks.map((track) => <button className={selectedSubtitleTrackId === track.id ? "active" : ""} disabled={proofreadBusy || subtitleSaving} key={track.id} onClick={() => void selectSubtitleTrack(track.id)} type="button"><strong>{track.name}</strong><small>{track.language.toUpperCase()} · {track.cues.length} sous-titre{track.cues.length > 1 ? "s" : ""}</small></button>)}</div> : <p>Aucune piste n’est encore enregistrée. Personnalisez la piste proposée puis sauvegardez-la.</p>}
              </section>
              <div className="subtitle-options">
                <label className="subtitle-name">Nom de la piste<input maxLength={180} onChange={(event) => { setSubtitleTrackName(event.target.value); setSubtitleDirty(true); }} placeholder="Ex. Français corrigé" value={subtitleTrackName} /></label>
                <label>Langue<input maxLength={20} onChange={(event) => { setSubtitleDraft({ ...subtitleDraft, language: event.target.value }); setSubtitleDirty(true); }} value={subtitleDraft.language} /></label>
                <label>Police<select onChange={(event) => { setSubtitleDraft({ ...subtitleDraft, font: event.target.value as JobAnalysis["subtitles"]["font"] }); setSubtitleDirty(true); }} value={subtitleDraft.font}><option value="sans">Sans serif</option><option value="serif">Serif</option><option value="mono">Monospace</option></select></label>
                <label>Couleur<input onChange={(event) => { setSubtitleDraft({ ...subtitleDraft, color: event.target.value }); setSubtitleDirty(true); }} type="color" value={subtitleDraft.color} /></label>
              </div>
              <div className="subtitle-cues">{scopedSubtitleCues.map((cue) => <label key={`${cue.sourceIndex}-${cue.start}`}><span>{formatDuration(cue.start)} → {formatDuration(cue.end)}</span><textarea rows={2} value={cue.text} onChange={(event) => { setSubtitleDraft({ ...subtitleDraft, cues: subtitleDraft.cues.map((item, position) => position === cue.sourceIndex ? { ...item, text: event.target.value } : item) }); setSubtitleDirty(true); }} /></label>)}</div>
              <div className="lab-next-actions"><button className="button secondary" onClick={() => void changeLab("audio-creation")}>Retour à Création Audio</button><button className="button accent" onClick={() => void changeLab("video-creation")}>Passer à Création Vidéo</button></div>
            </section>
          )}

          {activeLab === "audio-adjustment" && audioReady && exportJob && (
            <section className="studio-lab-panel audio-adjustment-lab" role="tabpanel">
              <div>
                <span className="eyebrow">Ajustement Audio · optionnel</span>
                <h2>Affiner l’audio avant la création vidéo</h2>
                <p>Vous pouvez ignorer cette étape et conserver exactement l’extrait produit dans Création Audio.</p>
              </div>
              <div className="adjustment-source"><div><span>Audio sélectionné</span><strong>{exportJob.content?.title || "Extrait audio"}</strong><small>{formatDuration(exportJob.metrics.duration_seconds)}</small></div><audio controls preload="metadata" src={api.artifactUrl(exportJob.id, "selection_audio")} /></div>
              <div className="adjustment-tools-preview">
                <article><span>↔</span><strong>Durée et silences</strong><p>Raccourcir l’extrait ou préparer des respirations entre les séquences.</p></article>
                <article><span>◉</span><strong>Volume et transitions</strong><p>Uniformiser le niveau sonore et adoucir les entrées et sorties.</p></article>
                <article><span>＋</span><strong>Ajout de voix</strong><p>Importer un complément vocal sans modifier l’original sauvegardé.</p></article>
              </div>
              <p className="lab-roadmap-note">Les outils de montage non destructif seront activés dans la prochaine étape de développement. L’audio original reste disponible.</p>
              <div className="lab-next-actions"><button className="button secondary" onClick={() => setActiveLab("audio-creation")}>Retour à Création Audio</button><button className="button accent" onClick={() => setActiveLab("video-creation")}>Passer sans ajustement</button></div>
            </section>
          )}

          {activeLab === "video-creation" && audioReady && (
            <section className="studio-lab-panel" role="tabpanel">
              <header className="editor-heading"><div><span className="eyebrow">Création Vidéo</span><h2>Associer un visuel à l’audio</h2><p>Importez une image finalisée dans Canva ou créez-en une depuis votre modèle visuel avec l’IA.</p></div><div className="selected-source-chip"><span>Audio</span><strong>{exportJob?.content?.title || analysis.parts[0]?.title || "Cours audio"}</strong><button onClick={() => setActiveLab("audio-creation")}>Changer</button></div></header>

              <section className="visual-mode-choice">
                <button className={visualMode === "ready" ? "active" : ""} onClick={() => setVisualMode("ready")} type="button"><span>01</span><strong>Image prête</strong><small>Visuel finalisé dans Canva, utilisé sans modification</small></button>
                <button className={visualMode === "ai" ? "active" : ""} onClick={() => setVisualMode("ai")} type="button"><span>02</span><strong>Génération IA</strong><small>Nouvelle image créée depuis un modèle visuel enregistré</small></button>
              </section>

              <section className="video-basics">
                <label>Titre du contenu<input maxLength={300} onChange={(event) => setVideoValues((current) => ({ ...current, title: event.target.value }))} placeholder={exportJob?.content?.title || analysis.parts[0]?.title || "Titre de l’audio"} value={videoValues.title} /></label>
                <div><span>Format de sortie</span><div className="format-choice" aria-label="Format de sortie">{(["16:9", "1:1", "9:16"] as const).map((format) => <button className={videoFormat === format ? "active" : ""} key={format} onClick={() => setVideoFormat(format)} type="button"><span className={`ratio ratio-${format.replace(":", "-")}`} />{format}</button>)}</div></div>
              </section>

              <TemplateLibrary
                mode={visualMode}
                onSelect={setSelectedTemplate}
                selectedId={selectedTemplate?.id || (visualMode === "ai" ? imageJob?.content?.template_id || "" : "")}
              />

              {visualMode === "ai" && (
                <section className="ai-image-panel">
                  <div className="ai-image-fields">
                    <div><span className="eyebrow">Direction créative</span><h3>Générer le visuel du cours</h3><p>L’IA reprend le style du modèle. Le titre est ajouté ensuite par Dars Manager pour rester parfaitement lisible.</p></div>
                    <label>Consigne complémentaire · optionnelle<textarea maxLength={1200} onChange={(event) => setImagePrompt(event.target.value)} placeholder="Exemple : ambiance nocturne, architecture islamique sobre, sans personnage…" rows={4} value={imagePrompt} /></label>
                    <button className="button primary" disabled={!selectedTemplate || generatingImage || imageBusy || !effectiveVideoTitle} onClick={createImage} type="button">{generatingImage ? "Préparation…" : imageBusy ? "Génération en cours…" : imageReady ? "Régénérer l’image" : "Générer l’image avec l’IA"}</button>
                  </div>
                  {imageJob && (
                    <div className={`generated-image-result ${imageJob.state}`}>
                      {imageReady ? <img alt="Visuel généré pour le cours" src={api.artifactUrl(imageJob.id, "generated_image")} /> : <div><span className={`job-state ${imageJob.state}`}>{imageJob.state}</span><strong>{imageJob.error || imageJob.message}</strong>{imageBusy && <div className="export-progress"><strong>{imageProgress}%</strong><div className="progress-track"><span style={{ width: `${imageProgress}%` }} /></div></div>}</div>}
                    </div>
                  )}
                </section>
              )}

              {visualMode === "ready" && selectedTemplate?.preview_url && (
                <section className="ready-image-preview"><img alt={`Visuel ${selectedTemplate.name}`} src={selectedTemplate.preview_url} /><div><span className="eyebrow">Aperçu</span><h3>{selectedTemplate.name}</h3><p>Cette image sera utilisée telle quelle. Le titre sert uniquement au nom du fichier et à la diffusion.</p></div></section>
              )}

              <section className="video-composer">
                <div><span className="eyebrow">Dernière étape</span><strong>{visualReady ? "Le visuel et l’audio sont prêts" : visualMode === "ai" ? "Générez et validez d’abord l’image" : "Choisissez ou importez une image prête"}</strong></div>
                <div className="video-subtitle-choice">
                  <label className={`subtitle-switch ${includeSubtitles ? "active" : ""}`}>
                    <input checked={includeSubtitles} disabled={subtitleTracks.length === 0} onChange={(event) => setIncludeSubtitles(event.target.checked)} type="checkbox" />
                    <span aria-hidden="true" className="subtitle-switch-track"><i /></span>
                    <span className="subtitle-switch-copy"><strong>Incruster les sous-titres</strong><small>{subtitleTracks.length === 0 ? "Préparez d’abord une piste pour cet audio" : includeSubtitles ? `${selectedVideoSubtitleCount} sous-titres seront ajoutés` : "Vidéo sans sous-titres"}</small></span>
                  </label>
                  {includeSubtitles && subtitleTracks.length > 0 && <label className="video-subtitle-select">Piste à utiliser<select onChange={(event) => setSelectedVideoSubtitleTrackId(event.target.value)} value={selectedVideoSubtitleTrackId}>{subtitleTracks.map((track) => <option key={track.id} value={track.id}>{track.name} · {track.language.toUpperCase()}</option>)}</select></label>}
                </div>
                <button className="button accent" disabled={!visualReady || dirty || (includeSubtitles && (subtitleDirty || proofreadBusy || !selectedVideoSubtitleTrack)) || renderingVideo || videoBusy || imageBusy} onClick={createVideo} type="button">{renderingVideo ? "Préparation…" : videoBusy ? "Rendu en cours…" : "Créer la vidéo"}</button>
              </section>
              {videoJob && (
                <div className={`video-render-status ${videoJob.state}`}>
                  <div><span className={`job-state ${videoJob.state}`}>{videoJob.state}</span><strong>{videoJob.state === "completed" ? "Vidéo prête à diffuser" : "Rendu vidéo"}</strong><small>{videoJob.error || videoJob.message}</small></div>
                  {videoJob.state === "completed" ? <div className="video-ready-actions"><video controls preload="metadata" src={api.artifactUrl(videoJob.id, "video")} /><button className="button accent" onClick={() => setActiveLab("distribution")}>Continuer vers Diffusion</button></div> : !terminalStates.has(videoJob.state) ? <div className="export-progress"><strong>{videoProgress}%</strong><div className="progress-track"><span style={{ width: `${videoProgress}%` }} /></div></div> : null}
                </div>
              )}
            </section>
          )}

          {activeLab === "distribution" && videoReady && videoJob && (
            <section className="studio-lab-panel distribution-lab" role="tabpanel">
              <header className="editor-heading"><div><span className="eyebrow">Diffusion</span><h2>Publier ou récupérer le contenu final</h2><p>Téléchargez la vidéo immédiatement. Les connexions YouTube et Telegram seront configurées ici.</p></div></header>
              <section className="final-content-card"><video controls preload="metadata" src={api.artifactUrl(videoJob.id, "video")} /><div><span>Contenu prêt</span><h3>{videoJob.content?.title || "Vidéo du cours"}</h3><p>Conservez le fichier ou préparez sa publication sur un canal connecté.</p><div>{videoJob.artifacts.includes("video") && <a className="button accent" download href={api.artifactUrl(videoJob.id, "video")}>Télécharger la vidéo</a>}{videoJob.artifacts.includes("cover") && <a className="button secondary" download href={api.artifactUrl(videoJob.id, "cover")}>Télécharger l’image</a>}</div></div></section>
              <div className="distribution-connectors">
                <article><span className="connector-mark youtube">▶</span><div><strong>YouTube</strong><p>Connecter une chaîne, choisir la visibilité et publier la vidéo.</p></div><button className="button secondary" disabled>À configurer</button></article>
                <article><span className="connector-mark telegram">➤</span><div><strong>Telegram</strong><p>Connecter un bot administrateur et publier dans une chaîne.</p></div><button className="button secondary" disabled>À configurer</button></article>
              </div>
              <section className="archive-panel"><div><span className="eyebrow">Sauvegarde complète</span><h3>Conserver le projet sur votre machine</h3><p>L’archive .dars réunit l’analyse corrigée, l’audio et le dernier rendu vidéo. Elle pourra être réimportée sans transcription.</p></div><button className="button primary compact" disabled={dirty || archiving || archiveBusy} onClick={createArchive} type="button">{archiving ? "Préparation…" : archiveBusy ? "Archivage en cours…" : "Créer l’archive .dars"}</button></section>
              {archiveJob && <div className={`archive-status ${archiveJob.state}`}><div><span className={`job-state ${archiveJob.state}`}>{archiveJob.state}</span><strong>{archiveJob.state === "completed" ? "Archive prête" : "Création de l’archive"}</strong><small>{archiveJob.error || archiveJob.message}</small></div>{archiveJob.state === "completed" && archiveJob.artifacts.includes("archive") ? <a className="button accent" download href={api.artifactUrl(archiveJob.id, "archive")}>Télécharger le .dars</a> : !terminalStates.has(archiveJob.state) ? <div className="export-progress"><strong>{archiveProgress}%</strong><div className="progress-track"><span style={{ width: `${archiveProgress}%` }} /></div></div> : null}</div>}
            </section>
          )}
        </>
      )}
    </section>
  );
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
    ["Financé par la communauté", summary.community_funded],
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
          <div><span className="eyebrow">Ventilation</span><h2>Coûts par projet</h2></div>
          <span>{summary.projects.length} projet{summary.projects.length > 1 ? "s" : ""}</span>
        </div>
        {summary.projects.length === 0 ? (
          <div className="empty-state compact-empty"><span className="empty-icon">◎</span><h3>Aucun coût attribué</h3><p>Les dépenses seront regroupées ici par projet.</p></div>
        ) : (
          <div className="project-cost-grid">
            {summary.projects.map((project) => (
              <article key={project.project_id || project.project_title}>
                <div><strong>{project.project_title}</strong><small>{project.operations} opération{project.operations > 1 ? "s" : ""}</small></div>
                <div><strong>{formatCurrency(project.amount_due, summary.currency)} dû</strong><small>{formatCurrency(project.confirmed_cost, summary.currency)} confirmé · {formatCurrency(project.community_funded, summary.currency)} financé par la communauté · {formatCurrency(project.estimated_cost, summary.currency)} estimé</small></div>
              </article>
            ))}
          </div>
        )}
      </section>
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
                  <td><span className={`cost-status ${item.status}`}>{({ confirmed: "Confirmé", estimated: "Estimé", reconciled: "Rapproché" })[item.status]}</span></td>
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
      {summary.community_allocations.length > 0 && (
        <section className="billing-section">
          <div className="section-heading"><div><span className="eyebrow">Communauté</span><h2>Financements attribués</h2></div></div>
          <div className="payment-list">{summary.community_allocations.map((allocation) => (
            <article key={allocation.id}>
              <div><strong>{allocation.project_title}</strong><small>{allocation.category} · {new Intl.DateTimeFormat("fr", { dateStyle: "long" }).format(new Date(allocation.created_at))}</small></div>
              <strong>{formatCurrency(allocation.amount, allocation.currency)}</strong>
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
  const [monthlyBudget, setMonthlyBudget] = useState("0");
  const [warningPercent, setWarningPercent] = useState(80);
  const [approvalThreshold, setApprovalThreshold] = useState("0");
  const [savingPolicy, setSavingPolicy] = useState(false);
  const [notice, setNotice] = useState("");

  useEffect(() => {
    setSummary(null);
    setError("");
    setNotice("");
    api.billingSummary(month)
      .then((value) => {
        setSummary(value);
        setMonthlyBudget(value.policy.monthly_budget);
        setWarningPercent(value.policy.warning_percent);
        setApprovalThreshold(value.policy.approval_threshold);
      })
      .catch((reason) => setError(reason instanceof Error ? reason.message : "Chargement impossible."));
  }, [month]);

  async function savePolicy(event: FormEvent) {
    event.preventDefault();
    setSavingPolicy(true);
    setError("");
    setNotice("");
    try {
      await api.updateBillingPolicy(monthlyBudget || "0", warningPercent, approvalThreshold || "0");
      const updated = await api.billingSummary(month);
      setSummary(updated);
      setMonthlyBudget(updated.policy.monthly_budget);
      setWarningPercent(updated.policy.warning_percent);
      setApprovalThreshold(updated.policy.approval_threshold);
      setNotice("Règles financières enregistrées.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Enregistrement impossible.");
    } finally {
      setSavingPolicy(false);
    }
  }

  return (
    <section className="billing-overview">
      <header className="workspace-header">
        <div><span className="eyebrow">Transparence</span><h1>Coûts cloud</h1><p>Suivez chaque dépense associée à vos productions.</p></div>
        <div className="billing-header-actions">
          <label className="month-picker">Période<input type="month" value={month} onChange={(event) => setMonth(event.target.value)} /></label>
          <div><a className="button secondary" download href={api.statementUrl("csv", month)}>Relevé CSV</a><a className="button secondary" download href={api.statementUrl("pdf", month)}>Relevé PDF</a></div>
        </div>
      </header>
      {error && <p className="form-error notice">{error}</p>}
      {notice && <p className="editor-feedback success">{notice}</p>}
      {!summary ? <div className="empty-state"><span className="loader" /><p>Calcul du relevé…</p></div> : <>
        <CostCards summary={summary} />
        <section className={`budget-panel ${summary.budget.state}`}>
          <div className="budget-overview">
            <div><span className="eyebrow">Budget mensuel</span><h2>{summary.policy.enabled ? `${formatCurrency(summary.budget.committed, summary.currency)} sur ${formatCurrency(summary.policy.monthly_budget, summary.currency)}` : "Aucun budget défini"}</h2><p>{summary.policy.enabled ? `${formatCurrency(summary.budget.remaining, summary.currency)} encore disponible · alerte à ${summary.policy.warning_percent}%` : "Définissez un montant pour suivre la consommation et recevoir un avertissement."}</p></div>
            {summary.policy.enabled && <strong>{Math.round(summary.budget.utilization_percent)}%</strong>}
          </div>
          {summary.policy.enabled && <div className="budget-track"><span style={{ width: `${Math.min(100, summary.budget.utilization_percent)}%` }} /></div>}
          <form className="budget-form" onSubmit={savePolicy}>
            <label>Budget mensuel USD<input min="0" step="0.01" type="number" value={monthlyBudget} onChange={(event) => setMonthlyBudget(event.target.value)} /></label>
            <label>Alerte à %<input min="1" max="100" type="number" value={warningPercent} onChange={(event) => setWarningPercent(Number(event.target.value))} /></label>
            <label>Confirmation dès USD<input min="0" step="0.01" type="number" value={approvalThreshold} onChange={(event) => setApprovalThreshold(event.target.value)} /></label>
            <button className="button primary compact" disabled={savingPolicy} type="submit">{savingPolicy ? "Enregistrement…" : "Enregistrer les seuils"}</button>
          </form>
        </section>
        <BillingDetails summary={summary} />
      </>}
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
  const [invoices, setInvoices] = useState<ProviderInvoice[]>([]);
  const [invoiceProvider, setInvoiceProvider] = useState("openai");
  const [invoiceService, setInvoiceService] = useState("transcription");
  const [invoiceReference, setInvoiceReference] = useState("");
  const [invoiceAmount, setInvoiceAmount] = useState("");
  const [invoiceTolerance, setInvoiceTolerance] = useState("0.000001");
  const [includeEstimated, setIncludeEstimated] = useState(false);
  const [invoiceNote, setInvoiceNote] = useState("");
  const [savingInvoice, setSavingInvoice] = useState(false);
  const [contributions, setContributions] = useState<CommunityContribution[]>([]);
  const [allocations, setAllocations] = useState<CommunityAllocation[]>([]);
  const [contributorName, setContributorName] = useState("");
  const [contributionAnonymous, setContributionAnonymous] = useState(false);
  const [contributionAmount, setContributionAmount] = useState("");
  const [contributionReference, setContributionReference] = useState("");
  const [contributionCampaign, setContributionCampaign] = useState("");
  const [contributionNote, setContributionNote] = useState("");
  const [savingContribution, setSavingContribution] = useState(false);
  const [allocationContribution, setAllocationContribution] = useState("");
  const [allocationProject, setAllocationProject] = useState("");
  const [allocationAmount, setAllocationAmount] = useState("");
  const [allocationCategory, setAllocationCategory] = useState("cloud_cost");
  const [allocationNote, setAllocationNote] = useState("");
  const [savingAllocation, setSavingAllocation] = useState(false);

  async function refresh() {
    setLoading(true);
    setError("");
    try {
      const [items, invoiceItems, contributionItems, allocationItems] = await Promise.all([
        api.clientBillingSummaries(month),
        api.providerInvoices(month),
        api.communityContributions(),
        api.communityAllocations(month),
      ]);
      setSummaries(items);
      setInvoices(invoiceItems);
      setContributions(contributionItems);
      setAllocations(allocationItems);
      setSelectedUser((current) => current || items[0]?.user.id || "");
      setAllocationContribution((current) => (
        contributionItems.some((item) => item.id === current && Number(item.remaining) > 0)
          ? current
          : contributionItems.find((item) => Number(item.remaining) > 0)?.id || ""
      ));
      const projectIds = new Set(items.flatMap((item) => item.projects
        .filter((project) => project.project_id && Number(project.confirmed_cost) > Number(project.community_funded))
        .map((project) => project.project_id as string)));
      setAllocationProject((current) => (
        current && projectIds.has(current)
          ? current
          : Array.from(projectIds)[0] || ""
      ));
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

  async function recordInvoice(event: FormEvent) {
    event.preventDefault();
    if (!invoiceProvider.trim() || !invoiceReference.trim() || !invoiceAmount) return;
    setSavingInvoice(true);
    setError("");
    try {
      await api.reconcileProviderInvoice({
        provider: invoiceProvider,
        service: invoiceService,
        reference: invoiceReference,
        period: month,
        invoiced_amount: invoiceAmount,
        tolerance: invoiceTolerance || "0",
        include_estimated: includeEstimated,
        note: invoiceNote,
      });
      setInvoiceReference("");
      setInvoiceAmount("");
      setInvoiceNote("");
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Rapprochement impossible.");
    } finally {
      setSavingInvoice(false);
    }
  }

  async function recordContribution(event: FormEvent) {
    event.preventDefault();
    if (!contributionAmount) return;
    setSavingContribution(true);
    setError("");
    try {
      await api.createCommunityContribution({
        contributor_name: contributorName,
        is_anonymous: contributionAnonymous,
        amount: contributionAmount,
        method: "manual",
        reference: contributionReference,
        campaign: contributionCampaign,
        note: contributionNote,
      });
      setContributorName("");
      setContributionAmount("");
      setContributionReference("");
      setContributionCampaign("");
      setContributionNote("");
      setContributionAnonymous(false);
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Enregistrement impossible.");
    } finally {
      setSavingContribution(false);
    }
  }

  async function recordAllocation(event: FormEvent) {
    event.preventDefault();
    if (!allocationContribution || !allocationProject || !allocationAmount) return;
    setSavingAllocation(true);
    setError("");
    try {
      await api.createCommunityAllocation({
        contribution_id: allocationContribution,
        project_id: allocationProject,
        period: month,
        amount: allocationAmount,
        category: allocationCategory,
        note: allocationNote,
      });
      setAllocationAmount("");
      setAllocationNote("");
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Allocation impossible.");
    } finally {
      setSavingAllocation(false);
    }
  }

  const selected = summaries.find((item) => item.user.id === selectedUser);
  const allocatableProjects = summaries.flatMap((item) => item.projects
    .filter((project) => project.project_id && Number(project.confirmed_cost) > Number(project.community_funded))
    .map((project) => ({
      id: project.project_id as string,
      label: `${item.user.display_name} — ${project.project_title}`,
      remaining: (Number(project.confirmed_cost) - Number(project.community_funded)).toFixed(6),
    })));
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
      <section className="provider-reconciliation">
        <div className="section-heading"><div><span className="eyebrow">Factures cloud</span><h2>Rapprochement fournisseur</h2></div><span>{invoices.length} facture{invoices.length > 1 ? "s" : ""}</span></div>
        <form className="invoice-form" onSubmit={recordInvoice}>
          <label>Fournisseur<input maxLength={80} required value={invoiceProvider} onChange={(event) => setInvoiceProvider(event.target.value)} /></label>
          <label>Service<input maxLength={80} placeholder="Tous si vide" value={invoiceService} onChange={(event) => setInvoiceService(event.target.value)} /></label>
          <label>Référence facture<input maxLength={180} required value={invoiceReference} onChange={(event) => setInvoiceReference(event.target.value)} /></label>
          <label>Montant facturé USD<input min="0" step="0.000001" required type="number" value={invoiceAmount} onChange={(event) => setInvoiceAmount(event.target.value)} /></label>
          <label>Tolérance USD<input min="0" step="0.000001" type="number" value={invoiceTolerance} onChange={(event) => setInvoiceTolerance(event.target.value)} /></label>
          <label className="invoice-checkbox"><input checked={includeEstimated} onChange={(event) => setIncludeEstimated(event.target.checked)} type="checkbox" /><span>Inclure les coûts estimés mesurés</span></label>
          <label className="invoice-note">Note<textarea maxLength={4000} rows={2} value={invoiceNote} onChange={(event) => setInvoiceNote(event.target.value)} /></label>
          <button className="button primary compact" disabled={savingInvoice} type="submit">{savingInvoice ? "Calcul…" : "Enregistrer et rapprocher"}</button>
        </form>
        {invoices.length > 0 && (
          <div className="billing-table-wrap invoice-table-wrap">
            <table className="billing-table"><thead><tr><th>Facture</th><th>Périmètre</th><th>Interne</th><th>Facturé</th><th>Écart</th><th>Statut</th></tr></thead><tbody>{invoices.map((invoice) => (
              <tr key={invoice.id}>
                <td><strong>{invoice.reference}</strong><small>{new Intl.DateTimeFormat("fr", { dateStyle: "medium" }).format(new Date(invoice.created_at))}</small></td>
                <td>{invoice.provider}<small>{invoice.service || "Tous services"}{invoice.include_estimated ? " · estimations incluses" : " · confirmé"}</small></td>
                <td>{formatCurrency(invoice.internal_amount, invoice.currency)}</td>
                <td>{formatCurrency(invoice.invoiced_amount, invoice.currency)}</td>
                <td><strong>{formatCurrency(invoice.variance, invoice.currency)}</strong></td>
                <td><span className={`reconciliation-status ${invoice.status}`}>{invoice.status === "matched" ? "Rapproché" : "Écart"}</span></td>
              </tr>
            ))}</tbody></table>
          </div>
        )}
      </section>
      <section className="community-finance">
        <div className="section-heading"><div><span className="eyebrow">Soutien communautaire</span><h2>Contributions et allocations</h2><p>Les écritures enregistrées restent auditables ; une allocation finance un coût sans le supprimer.</p></div><span>{contributions.length} contribution{contributions.length > 1 ? "s" : ""}</span></div>
        <div className="community-form-grid">
          <form className="community-form" onSubmit={recordContribution}>
            <div><span className="eyebrow">Encaissement manuel</span><h3>Enregistrer une contribution</h3></div>
            <label>Contributeur<input disabled={contributionAnonymous} maxLength={180} placeholder="Nom facultatif" value={contributorName} onChange={(event) => setContributorName(event.target.value)} /></label>
            <label>Montant reçu USD<input min="0.000001" step="0.000001" required type="number" value={contributionAmount} onChange={(event) => setContributionAmount(event.target.value)} /></label>
            <label>Référence<input maxLength={180} placeholder="Reçu, virement…" value={contributionReference} onChange={(event) => setContributionReference(event.target.value)} /></label>
            <label>Campagne<input maxLength={180} placeholder="Général si vide" value={contributionCampaign} onChange={(event) => setContributionCampaign(event.target.value)} /></label>
            <label className="invoice-checkbox"><input checked={contributionAnonymous} onChange={(event) => setContributionAnonymous(event.target.checked)} type="checkbox" /><span>Contributeur anonyme</span></label>
            <label className="community-note">Note<textarea maxLength={4000} rows={2} value={contributionNote} onChange={(event) => setContributionNote(event.target.value)} /></label>
            <button className="button primary compact" disabled={savingContribution} type="submit">{savingContribution ? "Enregistrement…" : "Enregistrer la contribution"}</button>
          </form>
          <form className="community-form" onSubmit={recordAllocation}>
            <div><span className="eyebrow">Affectation</span><h3>Financer un coût confirmé</h3></div>
            <label>Contribution<select required value={allocationContribution} onChange={(event) => setAllocationContribution(event.target.value)}><option value="">Sélectionner</option>{contributions.filter((item) => Number(item.remaining) > 0).map((item) => <option key={item.id} value={item.id}>{item.contributor_display} · {formatCurrency(item.remaining, item.currency)} disponible</option>)}</select></label>
            <label>Projet<select required value={allocationProject} onChange={(event) => setAllocationProject(event.target.value)}><option value="">Sélectionner</option>{allocatableProjects.map((project) => <option key={project.id} value={project.id}>{project.label} · {formatCurrency(project.remaining, "USD")} restant</option>)}</select></label>
            <label>Montant alloué USD<input min="0.000001" step="0.000001" required type="number" value={allocationAmount} onChange={(event) => setAllocationAmount(event.target.value)} /></label>
            <label>Catégorie<select value={allocationCategory} onChange={(event) => setAllocationCategory(event.target.value)}><option value="cloud_cost">Coûts cloud</option><option value="transcription">Transcription</option><option value="storage">Stockage</option><option value="publication">Publication</option></select></label>
            <label className="community-note">Note<textarea maxLength={4000} rows={2} value={allocationNote} onChange={(event) => setAllocationNote(event.target.value)} /></label>
            <button className="button primary compact" disabled={savingAllocation || !allocationContribution || !allocationProject} type="submit">{savingAllocation ? "Allocation…" : "Allouer la contribution"}</button>
          </form>
        </div>
        <div className="community-ledgers">
          <div>
            <h3>Contributions reçues</h3>
            {contributions.length === 0 ? <p className="muted-line">Aucune contribution enregistrée.</p> : <div className="billing-table-wrap"><table className="billing-table"><thead><tr><th>Contributeur</th><th>Reçu</th><th>Alloué</th><th>Disponible</th></tr></thead><tbody>{contributions.map((item) => <tr key={item.id}><td><strong>{item.contributor_display}</strong><small>{item.campaign || item.reference || "Soutien général"}</small></td><td>{formatCurrency(item.amount, item.currency)}</td><td>{formatCurrency(item.allocated, item.currency)}</td><td><strong>{formatCurrency(item.remaining, item.currency)}</strong></td></tr>)}</tbody></table></div>}
          </div>
          <div>
            <h3>Allocations de {month}</h3>
            {allocations.length === 0 ? <p className="muted-line">Aucune allocation sur cette période.</p> : <div className="billing-table-wrap"><table className="billing-table"><thead><tr><th>Origine</th><th>Projet</th><th>Catégorie</th><th>Montant</th></tr></thead><tbody>{allocations.map((item) => <tr key={item.id}><td>{item.contributor_display || "Communauté"}</td><td><strong>{item.project_title}</strong></td><td>{item.category}</td><td><strong>{formatCurrency(item.amount, item.currency)}</strong></td></tr>)}</tbody></table></div>}
          </div>
        </div>
      </section>
    </section>
  );
}

function ProjectWorkspace({ project, onBack, onEdit }: { project: Project; onBack: () => void; onEdit: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [quote, setQuote] = useState<TranscriptionQuote | null>(null);
  const [quoteLoading, setQuoteLoading] = useState(false);
  const [job, setJob] = useState<Job | null | undefined>(undefined);
  const [submitting, setSubmitting] = useState(false);
  const [uploadStage, setUploadStage] = useState<"reserve" | "upload" | "validate" | "start" | null>(null);
  const [actionPending, setActionPending] = useState(false);
  const [error, setError] = useState("");
  const [costConfirmed, setCostConfirmed] = useState(false);
  const [transcriptionMode, setTranscriptionMode] = useState<"cloud" | "local">("cloud");
  const [chapteringMode, setChapteringMode] = useState<"ai" | "local" | "none">("ai");
  const [courseTitle, setCourseTitle] = useState("");
  const fileIsArchive = Boolean(file?.name.toLowerCase().endsWith(".dars"));

  useEffect(() => {
    let active = true;
    setQuote(null);
    setCostConfirmed(false);
    if (!file) return () => { active = false; };
    if (file.name.toLowerCase().endsWith(".dars")) {
      setQuoteLoading(false);
      setError("");
      return () => { active = false; };
    }
    setQuoteLoading(true);
    setError("");
    inspectAudioDuration(file)
      .then((duration) => api.quoteTranscription(duration, transcriptionMode, chapteringMode))
      .then((value) => { if (active) setQuote(value); })
      .catch((reason) => {
        if (active) setError(reason instanceof Error ? reason.message : "Estimation impossible.");
      })
      .finally(() => { if (active) setQuoteLoading(false); });
    return () => { active = false; };
  }, [file, transcriptionMode, chapteringMode]);

  useEffect(() => {
    let active = true;
    setJob(undefined);
    api.jobs(project.id)
      .then((jobs) => active && setJob(jobs.find((item) => (
        item.tool === "audio_pipeline" || item.tool === "archive_import"
      )) ?? null))
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
    if (!file || (!fileIsArchive && !quote)) return;
    setSubmitting(true);
    setError("");
    try {
      setJob(fileIsArchive
        ? await api.importArchive(project.id, file, setUploadStage)
        : await api.createJob(
          project.id,
          file,
          "fr",
          quote!.duration_seconds,
          transcriptionMode,
          chapteringMode,
          courseTitle,
          costConfirmed,
          setUploadStage,
        ));
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
            <div><h2>Importer le cours</h2><p>Choisissez un nouvel audio ou restaurez une archive Dars Manager.</p></div>
          </div>
          <label className={`drop-zone ${file ? "has-file" : ""}`}>
            <input
              type="file"
              accept="audio/*,.aac,.m4a,.mp3,.mpeg,.mpga,.wav,.ogg,.opus,.flac,.dars,application/zip"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              required
            />
            <span className="upload-icon">↑</span>
            <strong>{file ? file.name : "Sélectionner un audio ou une archive .dars"}</strong>
            <small>{file ? `${(file.size / 1024 / 1024).toFixed(1)} Mo` : "Audio ou .dars · 500 Mo maximum"}</small>
          </label>
          {!fileIsArchive && (
            <div className="processing-modes">
              <fieldset className="mode-group">
                <legend>Mode de transcription</legend>
                <div className="mode-options">
                  <label className={transcriptionMode === "cloud" ? "mode-option active" : "mode-option"}>
                    <input checked={transcriptionMode === "cloud"} name="transcription-mode" onChange={() => setTranscriptionMode("cloud")} type="radio" />
                    <span><strong>Cloud — recommandé</strong><small><b>Avantages :</b> meilleure précision, plus rapide, particulièrement sur les noms et les passages multilingues.</small><small><b>Inconvénient :</b> facturation selon la durée de l’audio.</small></span>
                  </label>
                  <label className={transcriptionMode === "local" ? "mode-option active" : "mode-option"}>
                    <input checked={transcriptionMode === "local"} name="transcription-mode" onChange={() => setTranscriptionMode("local")} type="radio" />
                    <span><strong>Serveur local — économique</strong><small><b>Avantages :</b> aucun appel de transcription facturé, traitement sur le serveur Dars Manager.</small><small><b>Inconvénients :</b> le temps de transcription est plus long et la qualité peut varier. Si plusieurs utilisateurs choisissent ce mode, les traitements sont placés dans une file d’attente et exécutés l’un après l’autre.</small></span>
                  </label>
                </div>
              </fieldset>
              <fieldset className="mode-group">
                <legend>Mode de chapitrage</legend>
                <div className="mode-options">
                  <label className={chapteringMode === "ai" ? "mode-option active" : "mode-option"}>
                    <input checked={chapteringMode === "ai"} name="chaptering-mode" onChange={() => setChapteringMode("ai")} type="radio" />
                    <span><strong>IA — recommandé</strong><small><b>Avantages :</b> meilleure compréhension des sous-sujets, titres et résumés plus éloquents.</small><small><b>Inconvénient :</b> faible coût supplémentaire calculé selon le texte.</small></span>
                  </label>
                  <label className={chapteringMode === "local" ? "mode-option active" : "mode-option"}>
                    <input checked={chapteringMode === "local"} name="chaptering-mode" onChange={() => setChapteringMode("local")} type="radio" />
                    <span><strong>Script local — sans coût IA</strong><small><b>Avantages :</b> aucun appel facturé, résultat déterministe fondé sur les ruptures de vocabulaire.</small><small><b>Inconvénient :</b> titres plus simples et changements de sujet moins finement compris.</small></span>
                  </label>
                  <label className={chapteringMode === "none" ? "mode-option active" : "mode-option"}>
                    <input checked={chapteringMode === "none"} name="chaptering-mode" onChange={() => setChapteringMode("none")} type="radio" />
                    <span><strong>Sans chapitrage — audio déjà prêt</strong><small>Le cours entier est transcrit et reste utilisable tel quel. Vous choisissez son titre.</small><small>Aucun coût de chapitrage ni découpage imposé.</small></span>
                  </label>
                </div>
              </fieldset>
              {chapteringMode === "none" && <label className="course-title-field">Titre du cours<input maxLength={180} onChange={(event) => setCourseTitle(event.target.value)} placeholder="Un titre évocateur pour cet audio" required value={courseTitle} /></label>}
            </div>
          )}
          <div className="upload-options">
            <div className="transcription-quote">
              <span>{fileIsArchive ? "Restauration du cours" : "Estimation du traitement choisi"}</span>
              {fileIsArchive ? (
                <>
                  <strong>0 coût de transcription</strong>
                  <small>L'analyse, l'audio et les rendus seront vérifiés puis restaurés.</small>
                </>
              ) : quoteLoading ? (
                <strong>Calcul en cours…</strong>
              ) : quote ? (
                <>
                  <strong>≈ {Number(quote.amount).toFixed(4)} {quote.currency}</strong>
                  <small>{formatDuration(quote.duration_seconds)} · transcription {quote.transcription_mode === "cloud" ? "cloud" : `locale (${quote.model})`} · {quote.chaptering_mode === "none" ? "sans chapitrage" : `chapitrage ${quote.chaptering_mode === "ai" ? "IA" : "local"}`}</small>
                  <small>
                    Transcription {Number(quote.transcription_amount).toFixed(4)} {quote.currency}
                    {` · chapitrage ${Number(quote.semantic_analysis.amount).toFixed(4)} ${quote.currency}`}
                  </small>
                  {quote.budget_state !== "disabled" && (
                    <small>Projection mensuelle : {formatCurrency(quote.monthly_projected, quote.currency)} / {formatCurrency(quote.monthly_budget, quote.currency)}</small>
                  )}
                </>
              ) : (
                <strong>Sélectionnez un audio valide</strong>
              )}
              {quote?.requires_confirmation && !fileIsArchive && (
                <label className="cost-confirmation">
                  <input checked={costConfirmed} onChange={(event) => setCostConfirmed(event.target.checked)} type="checkbox" />
                  <span>Je confirme cette dépense{quote.confirmation_reasons.includes("monthly_budget") ? " malgré le dépassement du budget mensuel" : " au-dessus du seuil défini"}.</span>
                </label>
              )}
            </div>
            <button className="button accent" disabled={!file || (!fileIsArchive && (!quote || (quote.requires_confirmation && !costConfirmed))) || submitting || quoteLoading} type="submit">
              {submitting ? ({
                reserve: "Préparation…",
                upload: "Envoi temporaire…",
                validate: "Vérification…",
                start: "Démarrage…",
              }[uploadStage || "reserve"]) : fileIsArchive ? "Restaurer le cours" : "Lancer le traitement"}
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
              {job.processing_modes && (
                <small>
                  Transcription {job.processing_modes.transcription === "cloud" ? "cloud" : "locale"}
                  {" · "}chapitrage {job.processing_modes.chaptering === "ai" ? "IA" : "local"}
                </small>
              )}
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

          {job.state === "completed" && job.artifacts.includes("analysis") && (
            <CourseEditor job={job} />
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

function AccountOverview({ user, onPasswordChanged }: { user: User; onPasswordChanged: () => void }) {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    if (newPassword !== confirmation) {
      setError("Les deux nouveaux mots de passe ne correspondent pas.");
      return;
    }
    setSaving(true);
    try {
      await api.changePassword(currentPassword, newPassword);
      onPasswordChanged();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Modification impossible.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="account-overview">
      <header className="workspace-header">
        <div><span className="eyebrow">Compte</span><h1>Paramètres de sécurité</h1><p>Gérez les accès à votre espace Dars Manager.</p></div>
      </header>
      <article className="account-card">
        <div className="account-identity"><span className="avatar">{user.display_name.charAt(0).toUpperCase()}</span><div><strong>{user.display_name}</strong><small>{user.email} · {user.role === "admin" ? "Administrateur" : "Client"}</small></div></div>
        <div className="account-security-copy"><span className="eyebrow">Mot de passe</span><h2>Changer mon mot de passe</h2><p>La modification ferme toutes vos sessions. Vous devrez vous reconnecter avec le nouveau mot de passe.</p></div>
        <form onSubmit={submit}>
          <PasswordField autoComplete="current-password" label="Mot de passe actuel" onChange={setCurrentPassword} value={currentPassword} />
          <PasswordField label="Nouveau mot de passe" minLength={10} onChange={setNewPassword} value={newPassword} />
          <PasswordField label="Confirmer le nouveau mot de passe" minLength={10} onChange={setConfirmation} value={confirmation} />
          {error && <p className="form-error notice">{error}</p>}
          <button className="button primary" disabled={saving} type="submit">{saving ? "Modification…" : "Changer le mot de passe"}</button>
        </form>
      </article>
    </section>
  );
}

function Dashboard({ user, onLogout, onUserUpdated }: { user: User; onLogout: () => void; onUserUpdated: (user: User) => void }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState("");
  const [selectedProject, setSelectedProject] = useState<Project | null>(null);
  const [editingProject, setEditingProject] = useState<Project | null>(null);
  const [view, setView] = useState<"dashboard" | "jobs" | "billing" | "admin-billing" | "admin-users" | "account">(
    user.role === "admin" ? "admin-users" : "dashboard",
  );
  const [impact, setImpact] = useState<ImpactSummary | null>(null);

  useEffect(() => {
    if (user.role !== "client") {
      setLoading(false);
      return;
    }
    api.projects()
      .then(setProjects)
      .catch((reason) => setError(reason.message))
      .finally(() => setLoading(false));
  }, [user.role]);

  useEffect(() => {
    if (user.role !== "client" || selectedProject || view !== "dashboard") return;
    api.impactSummary().then(setImpact).catch(() => setImpact(null));
  }, [selectedProject, user.role, view]);

  useEffect(() => {
    if (loading) return;
    const restoreLocation = () => {
      if (user.role === "admin") {
        setSelectedProject(null);
        const adminView = {
          "#admin-users": "admin-users",
          "#admin-billing": "admin-billing",
          "#account": "account",
        }[window.location.hash] as typeof view | undefined;
        setView(adminView || "admin-users");
        return;
      }
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
        "#account": "account",
      }[window.location.hash] as typeof view | undefined;
      setView(hashView || "dashboard");
    };
    restoreLocation();
    window.addEventListener("hashchange", restoreLocation);
    return () => window.removeEventListener("hashchange", restoreLocation);
  }, [loading, projects, user.role]);

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
          {user.role === "client" ? <>
            <a className={!selectedProject && view === "dashboard" ? "active" : ""} href="#dashboard" onClick={() => showDashboard()}><span>⌂</span> Vue d’ensemble</a>
            <a href="#projects" onClick={() => showDashboard("projects")}><span>▱</span> Mes projets</a>
            <a href="#tools"><span>◇</span> Outils</a>
            <a className={view === "jobs" ? "active" : ""} href="#jobs" onClick={() => { setSelectedProject(null); setView("jobs"); }}><span>↻</span> Traitements</a>
            <a className={view === "billing" ? "active" : ""} href="#billing" onClick={() => { setSelectedProject(null); setView("billing"); }}><span>◉</span> Coûts</a>
          </> : <>
            <a className={view === "admin-users" ? "active" : ""} href="#admin-users" onClick={() => { setSelectedProject(null); setView("admin-users"); }}><span>◎</span> Comptes clients</a>
            <a className={view === "admin-billing" ? "active" : ""} href="#admin-billing" onClick={() => { setSelectedProject(null); setView("admin-billing"); }}><span>▤</span> Suivi financier</a>
          </>}
          <a className={view === "account" ? "active" : ""} href="#account" onClick={() => { setSelectedProject(null); setView("account"); }}><span>⚙</span> Paramètres</a>
        </nav>
        <div className="sidebar-user">
          <span className="avatar">{user.display_name.charAt(0).toUpperCase()}</span>
          <span><strong title={user.display_name}>{user.display_name}</strong><small title={user.email}>{user.email}</small></span>
          <button aria-label="Se déconnecter" onClick={onLogout}>↗</button>
        </div>
      </aside>

      <main className="workspace" id="dashboard">
        {user.role === "admin" && view === "admin-users" ? (
          <UserAdministration currentUser={user} onCurrentUserUpdated={onUserUpdated} />
        ) : user.role === "admin" && view === "admin-billing" ? (
          <AdminBillingOverview />
        ) : view === "account" ? (
          <AccountOverview user={user} onPasswordChanged={onLogout} />
        ) : selectedProject ? (
          <ProjectWorkspace project={selectedProject} onBack={() => showDashboard("projects")} onEdit={() => setEditingProject(selectedProject)} />
        ) : view === "jobs" ? (
          <JobsOverview projects={projects} onOpen={openProject} />
        ) : view === "billing" ? (
          <BillingOverview />
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

        {impact && (
          <section className="impact-strip">
            <header><div><span className="eyebrow">Impact de la semaine</span><strong>Du {new Intl.DateTimeFormat("fr", { day: "numeric", month: "short" }).format(new Date(`${impact.week_start}T00:00:00`))} au {new Intl.DateTimeFormat("fr", { day: "numeric", month: "short" }).format(new Date(`${impact.week_end}T00:00:00`))}</strong></div><small>Données privées · prêtes pour les futurs canaux publics</small></header>
            <div className="impact-cards">
              <article><span>Cours terminés</span><strong>{impact.courses_completed}</strong><small>{impact.courses_published} publié · {impact.videos_rendered} vidéo</small></article>
              <article><span>Heures produites</span><strong>{(impact.completed_duration_seconds / 3600).toFixed(1)} h</strong><small>{formatDuration(impact.completed_duration_seconds)} de cours</small></article>
              <article><span>Stockage cloud</span><strong>{formatBytes(impact.current_storage_bytes)}</strong><small>{formatBytes(impact.generated_storage_bytes)} générés cette semaine</small></article>
              <article><span>Coût de la semaine</span><strong>{formatCurrency(impact.cost, impact.currency)}</strong><small>confirmé + encore estimé</small></article>
            </div>
          </section>
        )}

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
  const [, setHash] = useState(window.location.hash);

  useEffect(() => {
    const changed = () => setHash(window.location.hash);
    window.addEventListener("hashchange", changed);
    return () => window.removeEventListener("hashchange", changed);
  }, []);

  useEffect(() => {
    api.me().then(setUser).catch(() => setUser(null)).finally(() => setChecking(false));
  }, []);

  async function logout() {
    await api.logout().catch(() => undefined);
    setUser(null);
  }

  const invitationToken = invitationTokenFromHash();
  if (invitationToken) return <InvitationAcceptance token={invitationToken} />;
  const resetToken = resetTokenFromHash();
  if (resetToken || window.location.hash === "#forgot-password") return <PasswordRecovery token={resetToken} />;
  if (checking) return <div className="boot"><Brand /><span className="loader" /></div>;
  return user ? <Dashboard user={user} onLogout={logout} onUserUpdated={setUser} /> : <Login onAuthenticated={setUser} />;
}
