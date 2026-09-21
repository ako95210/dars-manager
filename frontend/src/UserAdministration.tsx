import { FormEvent, useEffect, useMemo, useState } from "react";
import { AdminUser, api, EmailDeliveryStatus, User } from "./api";
import { PasswordField } from "./PasswordField";

type AccountDraft = {
  email: string;
  role: "client" | "admin";
  is_active: boolean;
  password: string;
  confirmation: string;
};

const emptyDraft: AccountDraft = {
  email: "",
  role: "client",
  is_active: false,
  password: "",
  confirmation: "",
};

function accountLabel(role: AdminUser["role"]) {
  return role === "admin" ? "Administrateur" : "Client";
}

function AccountEditor({
  account,
  currentUserId,
  onClose,
  onDeleted,
  onSaved,
}: {
  account: AdminUser | null;
  currentUserId: string;
  onClose: () => void;
  onDeleted: (accountId: string) => void;
  onSaved: (account: AdminUser) => void;
}) {
  const [draft, setDraft] = useState<AccountDraft>(account ? {
    email: account.email,
    role: account.role,
    is_active: account.is_active,
    password: "",
    confirmation: "",
  } : emptyDraft);
  const [saving, setSaving] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteConfirmation, setDeleteConfirmation] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const isCurrentUser = account?.id === currentUserId;

  function update<K extends keyof AccountDraft>(field: K, value: AccountDraft[K]) {
    setDraft((current) => ({ ...current, [field]: value }));
    setError("");
    setNotice("");
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setSaving(true);
    try {
      const saved = account
        ? await api.updateAdminUser(account.id, {
          email: draft.email,
          role: draft.role,
          is_active: draft.is_active,
        })
        : await api.createAdminUser({
          email: draft.email,
        });
      onSaved(saved);
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Enregistrement impossible.");
    } finally {
      setSaving(false);
    }
  }

  async function resetPassword(event: FormEvent) {
    event.preventDefault();
    if (!account) return;
    setError("");
    setNotice("");
    if (draft.password !== draft.confirmation) {
      setError("Les deux mots de passe ne correspondent pas.");
      return;
    }
    setResetting(true);
    try {
      await api.resetAdminUserPassword(account.id, draft.password);
      setDraft((current) => ({ ...current, password: "", confirmation: "" }));
      setNotice("Mot de passe réinitialisé. Toutes les sessions de ce compte ont été fermées.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Réinitialisation impossible.");
    } finally {
      setResetting(false);
    }
  }

  async function deleteAccount() {
    if (!account) return;
    setDeleting(true);
    setError("");
    try {
      await api.deleteAdminUser(account.id);
      onDeleted(account.id);
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Suppression impossible.");
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <article aria-labelledby="account-editor-title" aria-modal="true" className="project-editor account-editor" role="dialog">
        <header>
          <div>
            <span className="eyebrow">Administration</span>
            <h2 id="account-editor-title">{account ? "Modifier le compte" : "Nouveau compte"}</h2>
          </div>
          <button aria-label="Fermer" className="icon-button" onClick={onClose} type="button">×</button>
        </header>
        <form onSubmit={submit}>
          <div className="account-form-grid">
            {account && <label>Nom affiché<input disabled value={account.display_name} /></label>}
            <label>Adresse e-mail<input autoComplete="email" maxLength={320} required type="email" value={draft.email} onChange={(event) => update("email", event.target.value)} /></label>
            {account && <label>Rôle<select disabled={isCurrentUser} value={draft.role} onChange={(event) => update("role", event.target.value as AccountDraft["role"])}><option value="client">Client</option><option value="admin">Administrateur</option></select></label>}
            {account && <label className="account-active-choice"><span>Accès au compte</span><span><input checked={draft.is_active} disabled={isCurrentUser || !account.email_verified_at} type="checkbox" onChange={(event) => update("is_active", event.target.checked)} /> {account.email_verified_at ? "Compte actif" : "En attente de vérification"}</span></label>}
          </div>
          {!account && (
            <div className="initial-password-fields">
              <p>Une invitation personnelle sera envoyée à cette adresse. Son destinataire choisira lui-même son nom affiché et son mot de passe avant d’accéder à son compte client.</p>
            </div>
          )}
          {error && <p className="form-error notice">{error}</p>}
          {notice && <p className="account-notice">{notice}</p>}
          <div className="modal-actions">
            <button className="button secondary" onClick={onClose} type="button">Annuler</button>
            <button className="button primary compact" disabled={saving} type="submit">{saving ? "Enregistrement…" : account ? "Enregistrer" : "Envoyer l’invitation"}</button>
          </div>
        </form>
        {account && !isCurrentUser && (
          <>
            {account.email_verified_at && <form className="password-reset-panel" onSubmit={resetPassword}>
              <div><strong>Réinitialiser le mot de passe</strong><p>Cette action ferme toutes les sessions actuellement ouvertes par cet utilisateur.</p></div>
              <div className="account-form-grid">
                <PasswordField label="Nouveau mot de passe" minLength={10} onChange={(value) => update("password", value)} value={draft.password} />
                <PasswordField label="Confirmation" minLength={10} onChange={(value) => update("confirmation", value)} value={draft.confirmation} />
              </div>
              <button className="danger-outline" disabled={resetting} type="submit">{resetting ? "Réinitialisation…" : "Réinitialiser"}</button>
            </form>}
            <div className="account-delete-panel">
              <div><strong>Supprimer le compte</strong><p>L’accès sera fermé et l’identité du compte anonymisée. L’historique financier sera conservé.</p></div>
              {deleteConfirmation ? (
                <div className="delete-confirmation"><span>Confirmer la suppression ?</span><button disabled={deleting} onClick={deleteAccount} className="danger" type="button">{deleting ? "Suppression…" : "Oui, supprimer"}</button><button onClick={() => setDeleteConfirmation(false)} type="button">Annuler</button></div>
              ) : <button className="danger-outline" onClick={() => setDeleteConfirmation(true)} type="button">Supprimer le compte</button>}
            </div>
          </>
        )}
      </article>
    </div>
  );
}

export function UserAdministration({
  currentUser,
  onCurrentUserUpdated,
}: {
  currentUser: User;
  onCurrentUserUpdated: (user: User) => void;
}) {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [selected, setSelected] = useState<AdminUser | null | undefined>(undefined);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [emailStatus, setEmailStatus] = useState<EmailDeliveryStatus | null>(null);
  const [sendingInvitationId, setSendingInvitationId] = useState<string | null>(null);

  function load() {
    setLoading(true);
    setError("");
    Promise.all([api.adminUsers(), api.emailDeliveryStatus()])
      .then(([accounts, delivery]) => {
        setUsers(accounts);
        setEmailStatus(delivery);
      })
      .catch((reason) => setError(reason instanceof Error ? reason.message : "Chargement impossible."))
      .finally(() => setLoading(false));
  }

  useEffect(load, []);

  const filtered = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase("fr");
    if (!needle) return users;
    return users.filter((account) => `${account.display_name} ${account.email}`.toLocaleLowerCase("fr").includes(needle));
  }, [query, users]);

  function saved(account: AdminUser) {
    setUsers((current) => {
      const exists = current.some((item) => item.id === account.id);
      return exists
        ? current.map((item) => item.id === account.id ? account : item)
        : [account, ...current];
    });
    if (account.id === currentUser.id) onCurrentUserUpdated(account);
  }

  function deleted(accountId: string) {
    setUsers((current) => current.map((account) => account.id === accountId ? {
      ...account,
      display_name: `Compte supprimé (${account.id.slice(0, 8)})`,
      email: `deleted-${account.id}@dars-manager.com`,
      role: account.role,
      is_active: false,
      deleted_at: new Date().toISOString(),
    } : account));
  }

  async function resendInvitation(account: AdminUser) {
    setSendingInvitationId(account.id);
    setError("");
    setNotice("");
    try {
      const updated = await api.resendAdminUserInvitation(account.id);
      saved(updated);
      setNotice(`Une nouvelle invitation a été envoyée à ${updated.email}.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Envoi impossible.");
    } finally {
      setSendingInvitationId(null);
    }
  }

  const activeCount = users.filter((account) => account.is_active && !account.deleted_at).length;
  const adminCount = users.filter((account) => account.role === "admin" && account.is_active).length;
  const deletedCount = users.filter((account) => account.deleted_at).length;
  const pendingCount = users.filter((account) => !account.deleted_at && !account.email_verified_at).length;

  return (
    <section className="user-administration">
      <header className="workspace-header">
        <div><span className="eyebrow">Administration</span><h1>Comptes utilisateurs</h1><p>Créez les accès clients et contrôlez les comptes existants.</p></div>
        <button className="button primary compact" disabled={!emailStatus?.configured} onClick={() => setSelected(null)}>＋ Ajouter un compte</button>
      </header>
      {emailStatus && !emailStatus.configured && <div className="email-setup-warning"><strong>Envoi d’e-mails à configurer</strong><p>La création de comptes est bloquée tant qu’un fournisseur SMTP n’est pas connecté à Dars Manager.</p></div>}
      {notice && <p className="account-notice account-page-notice">{notice}</p>}
      <div className="account-stat-grid">
        <article><span>Comptes</span><strong>{users.length}</strong><small>créés au total</small></article>
        <article><span>Actifs</span><strong>{activeCount}</strong><small>{users.length - activeCount - deletedCount - pendingCount} désactivé{users.length - activeCount - deletedCount - pendingCount > 1 ? "s" : ""} · {pendingCount} en attente</small></article>
        <article><span>Administrateurs</span><strong>{adminCount}</strong><small>avec accès actif</small></article>
      </div>
      <div className="account-list-toolbar">
        <div><span className="eyebrow">Annuaire</span><h2>Utilisateurs</h2></div>
        <input aria-label="Rechercher un compte" placeholder="Rechercher par nom ou e-mail" type="search" value={query} onChange={(event) => setQuery(event.target.value)} />
      </div>
      {error && <p className="form-error notice">{error} <button onClick={load}>Réessayer</button></p>}
      {loading ? (
        <div className="empty-state compact-empty"><span className="loader" /><p>Chargement des comptes…</p></div>
      ) : (
        <div className="billing-table-wrap account-table-wrap">
          <table className="billing-table account-table">
            <thead><tr><th>Utilisateur</th><th>Rôle</th><th>État</th><th>Création</th><th /></tr></thead>
            <tbody>
              {filtered.map((account) => (
                <tr key={account.id}>
                  <td><div className="account-table-identity"><span className="avatar">{account.display_name.charAt(0).toUpperCase()}</span><span><strong>{account.display_name}{account.id === currentUser.id && <em>Vous</em>}</strong><small>{account.email}</small></span></div></td>
                  <td><span className={`role-pill ${account.role}`}>{accountLabel(account.role)}</span></td>
                  <td><span className={`access-pill ${account.deleted_at ? "deleted" : !account.email_verified_at ? "pending" : account.is_active ? "active" : "inactive"}`}><i />{account.deleted_at ? "Supprimé" : !account.email_verified_at ? "En attente" : account.is_active ? "Actif" : "Désactivé"}</span></td>
                  <td>{new Intl.DateTimeFormat("fr", { dateStyle: "medium" }).format(new Date(account.created_at))}</td>
                  <td><div className="account-row-actions">{!account.deleted_at && !account.email_verified_at && <button className="table-action invitation-action" disabled={sendingInvitationId === account.id || !emailStatus?.configured} onClick={() => resendInvitation(account)}>{sendingInvitationId === account.id ? "Envoi…" : "Renvoyer"}</button>}<button className="table-action" disabled={Boolean(account.deleted_at)} onClick={() => setSelected(account)}>{account.deleted_at ? "Supprimé" : "Modifier"}</button></div></td>
                </tr>
              ))}
              {filtered.length === 0 && <tr><td className="account-table-empty" colSpan={5}>Aucun compte ne correspond à cette recherche.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
      {selected !== undefined && (
        <AccountEditor account={selected} currentUserId={currentUser.id} onClose={() => setSelected(undefined)} onDeleted={deleted} onSaved={saved} />
      )}
    </section>
  );
}
