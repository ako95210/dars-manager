import { FormEvent, useEffect, useState } from "react";
import { api, BrandTemplate, TemplateZone } from "./api";


function isVideo(file: File | null) {
  return Boolean(file && (
    file.type.startsWith("video/")
    || /\.(m4v|mov|mp4)$/i.test(file.name)
  ));
}

const zoneLabels: Record<TemplateZone["kind"], string> = {
  title: "Titre",
  speaker: "Intervenant",
  date: "Date",
  episode: "Épisode",
};

function defaultZone(kind: TemplateZone["kind"], position: number): TemplateZone {
  return {
    kind,
    x: 0.08,
    y: Math.min(0.82, 0.58 + position * 0.09),
    width: 0.84,
    height: kind === "title" ? 0.18 : 0.07,
    font_scale: kind === "title" ? 0.06 : 0.032,
    color: "#ffffff",
    align: "left",
  };
}


export function TemplateLibrary({
  selectedId,
  onSelect,
  outputFormat,
}: {
  selectedId: string;
  onSelect: (template: BrandTemplate | null) => void;
  outputFormat: "16:9" | "1:1" | "9:16";
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
  const [zones, setZones] = useState<TemplateZone[]>([]);
  const [savingZones, setSavingZones] = useState(false);

  const selected = templates.find((template) => template.id === selectedId) ?? null;

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

  useEffect(() => {
    setZones(selected?.zones ?? []);
  }, [selected?.id, selected?.version]);

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

  function toggleZone(kind: TemplateZone["kind"]) {
    setZones((current) => current.some((zone) => zone.kind === kind)
      ? current.filter((zone) => zone.kind !== kind)
      : [...current, defaultZone(kind, current.length)]);
  }

  function changeZone(kind: TemplateZone["kind"], field: keyof TemplateZone, value: string) {
    setZones((current) => current.map((zone) => {
      if (zone.kind !== kind) return zone;
      if (field === "color" || field === "align") return { ...zone, [field]: value };
      let numeric = Number(value);
      if (field === "x") numeric = Math.min(numeric, 1 - zone.width);
      if (field === "y") numeric = Math.min(numeric, 1 - zone.height);
      if (field === "width") numeric = Math.min(numeric, 1 - zone.x);
      if (field === "height") numeric = Math.min(numeric, 1 - zone.y);
      return { ...zone, [field]: numeric };
    }));
  }

  async function saveZones() {
    if (!selected) return;
    setSavingZones(true);
    setError("");
    try {
      const updated = await api.updateBrandTemplate(selected.id, selected.name, zones);
      setTemplates((current) => current.map((item) => item.id === updated.id ? updated : item));
      onSelect(updated);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Enregistrement impossible.");
    } finally {
      setSavingZones(false);
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

      {selected && (
        <section className="zone-editor">
          <div className={`zone-preview format-${outputFormat.replace(":", "-")}`} style={{ aspectRatio: ({ "16:9": "16 / 9", "1:1": "1 / 1", "9:16": "9 / 16" })[outputFormat] }}>
            {selected.preview_url && <img alt={`Aperçu ${selected.name}`} src={selected.preview_url} />}
            {zones.map((zone) => (
              <span
                className={`zone-overlay align-${zone.align}`}
                key={zone.kind}
                style={{
                  color: zone.color,
                  fontSize: `${Math.max(9, zone.font_scale * 380)}px`,
                  height: `${zone.height * 100}%`,
                  left: `${zone.x * 100}%`,
                  top: `${zone.y * 100}%`,
                  width: `${zone.width * 100}%`,
                }}
              >{zoneLabels[zone.kind]}</span>
            ))}
          </div>
          <div className="zone-controls">
            <header><div><strong>Zones dynamiques</strong><small>Position en pourcentage du cadre source</small></div><button className="button primary compact" disabled={savingZones} onClick={saveZones} type="button">{savingZones ? "Enregistrement…" : "Enregistrer les zones"}</button></header>
            {Object.entries(zoneLabels).map(([rawKind, label]) => {
              const kind = rawKind as TemplateZone["kind"];
              const zone = zones.find((item) => item.kind === kind);
              return (
                <details className="zone-row" key={kind} open={kind === "title"}>
                  <summary><label onClick={(event) => event.stopPropagation()}><input checked={Boolean(zone)} onChange={() => toggleZone(kind)} type="checkbox" /> {label}</label><span>{zone ? `${Math.round(zone.x * 100)}%, ${Math.round(zone.y * 100)}%` : "Masquée"}</span></summary>
                  {zone && <div className="zone-fields">
                    {(["x", "y", "width", "height"] as const).map((field) => <label key={field}>{({ x: "X", y: "Y", width: "Largeur", height: "Hauteur" })[field]}<input max="1" min={field === "width" || field === "height" ? "0.05" : "0"} onChange={(event) => changeZone(kind, field, event.target.value)} step="0.01" type="range" value={zone[field]} /></label>)}
                    <label>Taille<input max="0.2" min="0.015" onChange={(event) => changeZone(kind, "font_scale", event.target.value)} step="0.005" type="range" value={zone.font_scale} /></label>
                    <label>Couleur<input onChange={(event) => changeZone(kind, "color", event.target.value)} type="color" value={zone.color} /></label>
                    <label>Alignement<select onChange={(event) => changeZone(kind, "align", event.target.value)} value={zone.align}><option value="left">Gauche</option><option value="center">Centre</option><option value="right">Droite</option></select></label>
                  </div>}
                </details>
              );
            })}
          </div>
        </section>
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
