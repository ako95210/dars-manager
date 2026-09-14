import { FormEvent, useEffect, useState } from "react";
import { api, BrandTemplate } from "./api";


function isVideo(file: File | null) {
  return Boolean(file && (
    file.type.startsWith("video/")
    || /\.(m4v|mov|mp4)$/i.test(file.name)
  ));
}


export function TemplateLibrary({
  selectedId,
  onSelect,
}: {
  selectedId: string;
  onSelect: (template: BrandTemplate | null) => void;
}) {
  const [templates, setTemplates] = useState<BrandTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [usageMode, setUsageMode] = useState<"static_frame" | "animated">("static_frame");
  const [frameSeconds, setFrameSeconds] = useState("0");
  const [uploading, setUploading] = useState(false);
  const [stage, setStage] = useState<"reserve" | "upload" | "validate" | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    api.brandTemplates()
      .then((items) => {
        if (!active) return;
        setTemplates(items);
        if (!selectedId && items[0]) onSelect(items[0]);
      })
      .catch((reason) => {
        if (active) setError(reason instanceof Error ? reason.message : "Chargement impossible.");
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  function chooseFile(next: File | null) {
    setFile(next);
    setError("");
    if (next && !name) setName(next.name.replace(/\.[^.]+$/, ""));
    if (!isVideo(next)) setUsageMode("static_frame");
  }

  async function upload(event: FormEvent) {
    event.preventDefault();
    if (!file || !name.trim()) return;
    setUploading(true);
    setError("");
    try {
      const template = await api.createBrandTemplate(
        name.trim(),
        file,
        usageMode,
        Number(frameSeconds) || 0,
        setStage,
      );
      setTemplates((current) => [template, ...current]);
      onSelect(template);
      setFile(null);
      setName("");
      setUsageMode("static_frame");
      setFrameSeconds("0");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Import impossible.");
    } finally {
      setUploading(false);
      setStage(null);
    }
  }

  async function remove(template: BrandTemplate) {
    setError("");
    try {
      await api.deleteBrandTemplate(template.id);
      const remaining = templates.filter((item) => item.id !== template.id);
      setTemplates(remaining);
      if (selectedId === template.id) onSelect(remaining[0] ?? null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Suppression impossible.");
    }
  }

  return (
    <section className="template-library">
      <header>
        <div><span className="eyebrow">Identité visuelle</span><h3>Templates vidéo</h3><p>Choisissez un design existant ou importez une nouvelle référence.</p></div>
      </header>

      {loading ? (
        <div className="template-loading"><span className="loader" /> Chargement des templates…</div>
      ) : templates.length > 0 ? (
        <div className="template-grid">
          {templates.map((template) => (
            <article className={selectedId === template.id ? "selected" : ""} key={template.id}>
              <button className="template-choice" onClick={() => onSelect(template)} type="button">
                {template.preview_url ? <img alt="" src={template.preview_url} /> : <span className="template-placeholder">◇</span>}
                <span className="template-info">
                  <strong>{template.name}</strong>
                  <small>{template.source_kind === "video" ? (template.usage_mode === "animated" ? "Vidéo animée" : "Image extraite d’une vidéo") : "Image fixe"} · v{template.version}</small>
                </span>
                <span className="template-selected">✓</span>
              </button>
              <button aria-label={`Supprimer ${template.name}`} className="template-delete" onClick={() => remove(template)} type="button">×</button>
            </article>
          ))}
        </div>
      ) : (
        <p className="template-empty">Aucun template enregistré. Importez votre première identité visuelle.</p>
      )}

      <form className="template-upload" onSubmit={upload}>
        <label className={`template-drop ${file ? "has-file" : ""}`}>
          <input accept="image/png,image/jpeg,video/mp4,video/quicktime,.m4v,.mov" onChange={(event) => chooseFile(event.target.files?.[0] ?? null)} type="file" />
          <span>＋</span><strong>{file ? file.name : "Ajouter une image ou une vidéo"}</strong><small>PNG/JPEG jusqu’à 25 Mo · MP4/MOV jusqu’à 500 Mo</small>
        </label>
        {file && (
          <div className="template-upload-fields">
            <label>Nom du template<input maxLength={180} onChange={(event) => setName(event.target.value)} required value={name} /></label>
            {isVideo(file) && (
              <>
                <label>Utilisation<select onChange={(event) => setUsageMode(event.target.value as "static_frame" | "animated")} value={usageMode}><option value="static_frame">Extraire une image fixe</option><option value="animated">Conserver le fond animé</option></select></label>
                <label>Image de référence à la seconde<input min="0" onChange={(event) => setFrameSeconds(event.target.value)} step="0.1" type="number" value={frameSeconds} /></label>
              </>
            )}
            <button className="button primary compact" disabled={uploading} type="submit">{uploading ? ({ reserve: "Préparation…", upload: "Envoi…", validate: "Validation du média…" }[stage || "reserve"]) : "Enregistrer le template"}</button>
          </div>
        )}
      </form>
      {error && <p className="editor-feedback error">{error}</p>}
    </section>
  );
}
