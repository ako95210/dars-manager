import { FormEvent, useEffect, useMemo, useState } from "react";
import { api, BrandTemplate } from "./api";


export type VisualMode = "ready" | "ai";

export function TemplateLibrary({
  selectedId,
  onSelect,
  mode,
}: {
  selectedId: string;
  onSelect: (template: BrandTemplate | null) => void;
  mode: VisualMode;
}) {
  const [templates, setTemplates] = useState<BrandTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [uploading, setUploading] = useState(false);
  const [stage, setStage] = useState<"reserve" | "upload" | "validate" | null>(null);
  const [error, setError] = useState("");

  const imageTemplates = useMemo(
    () => templates.filter((template) => template.source_kind === "image"),
    [templates],
  );

  useEffect(() => {
    let active = true;
    api.brandTemplates()
      .then((items) => {
        if (active) setTemplates(items);
      })
      .catch((reason) => {
        if (active) setError(reason instanceof Error ? reason.message : "Chargement impossible.");
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (loading) return;
    const selected = imageTemplates.find((template) => template.id === selectedId);
    onSelect(selected ?? imageTemplates[0] ?? null);
  }, [loading, mode, imageTemplates.length, selectedId]);

  function chooseFile(next: File | null) {
    setFile(next);
    setError("");
    if (next && !name) setName(next.name.replace(/\.[^.]+$/, ""));
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
        "static_frame",
        0,
        mode === "ready" ? "ready_image" : "ai_reference",
        setStage,
      );
      setTemplates((current) => [template, ...current]);
      onSelect(template);
      setFile(null);
      setName("");
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
      if (selectedId === template.id) {
        onSelect(remaining.find((item) => item.source_kind === "image") ?? null);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Suppression impossible.");
    }
  }

  const wording = mode === "ready"
    ? {
      eyebrow: "Image prête",
      title: "Visuels créés dans Canva",
      description: "Choisissez une image finalisée. Dars Manager l'utilisera sans ajouter ni déplacer de texte.",
      empty: "Aucun visuel prêt. Importez une image finalisée depuis Canva.",
      add: "Importer une image prête",
      submit: "Enregistrer le visuel",
    }
    : {
      eyebrow: "Modèle IA",
      title: "Références visuelles",
      description: "Choisissez une image qui définit le style, les couleurs et la composition à conserver.",
      empty: "Aucun modèle visuel. Importez une image de référence pour guider l'IA.",
      add: "Importer un modèle visuel",
      submit: "Enregistrer le modèle",
    };

  return (
    <section className="template-library simplified-template-library">
      <header>
        <div><span className="eyebrow">{wording.eyebrow}</span><h3>{wording.title}</h3><p>{wording.description}</p></div>
      </header>

      {loading ? (
        <div className="template-loading"><span className="loader" /> Chargement des images…</div>
      ) : imageTemplates.length > 0 ? (
        <div className="template-grid">
          {imageTemplates.map((template) => (
            <article className={selectedId === template.id ? "selected" : ""} key={template.id}>
              <button className="template-choice" onClick={() => onSelect(template)} type="button">
                {template.preview_url ? <img alt="" src={template.preview_url} /> : <span className="template-placeholder">◇</span>}
                <span className="template-info">
                  <strong>{template.name}</strong>
                  <small>{template.purpose === "ai_reference" ? "Modèle IA" : template.purpose === "ready_image" ? "Image prête" : "Ancien template"}</small>
                </span>
                <span className="template-selected">✓</span>
              </button>
              <button aria-label={`Supprimer ${template.name}`} className="template-delete" onClick={() => remove(template)} type="button">×</button>
            </article>
          ))}
        </div>
      ) : (
        <p className="template-empty">{wording.empty}</p>
      )}

      <form className="template-upload" onSubmit={upload}>
        <label className={`template-drop ${file ? "has-file" : ""}`}>
          <input accept="image/png,image/jpeg,.jpg,.jpeg,.png" onChange={(event) => chooseFile(event.target.files?.[0] ?? null)} type="file" />
          <span>＋</span><strong>{file ? file.name : wording.add}</strong><small>PNG ou JPEG · 25 Mo maximum</small>
        </label>
        {file && (
          <div className="template-upload-fields simplified-upload-fields">
            <label>Nom<input maxLength={180} onChange={(event) => setName(event.target.value)} required value={name} /></label>
            <button className="button primary compact" disabled={uploading} type="submit">{uploading ? ({ reserve: "Préparation…", upload: "Envoi…", validate: "Validation de l’image…" }[stage || "reserve"]) : wording.submit}</button>
          </div>
        )}
      </form>
      {error && <p className="editor-feedback error">{error}</p>}
    </section>
  );
}
