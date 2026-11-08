<script lang="ts">
  import type { LoginRequest, User } from '@contract-ocr/shared-types';
  import Banner from '../components/Banner.svelte';
  import { api, failureCode } from '../services/api.ts';
  import { errorLabel } from '../utils/labels.ts';
  import { TEST_ACCOUNTS, initials } from '../utils/testAccounts.ts';

  // Mock sign-in: a stand-in for Apps Gateway (the company SSO). It asks for no password, so nobody types a real one
  // into a page that does not check it.
  let { onSignedIn, notice = null }: { onSignedIn: (user: User) => void; notice?: string | null } = $props();

  let email = $state('');
  let name = $state('');
  let busy = $state(false);
  let error = $state<string | null>(null);
  // Quick login with a fictitious test account: a development build only, never a build that could be deployed.
  const quick = import.meta.env.DEV && TEST_ACCOUNTS.length > 0;
  let accountsOpen = $state(false);
  let signingAs = $state<string | null>(null); // the test account being signed in, for its row's label

  async function signIn(identity: LoginRequest) {
    if (busy) return;
    busy = true;
    error = null;
    try {
      const { user } = await api.login(identity);
      onSignedIn(user);
    } catch (e) {
      error = failureCode(e);
    } finally {
      busy = false;
      signingAs = null;
    }
  }

  function submit(event: SubmitEvent) {
    event.preventDefault();
    void signIn({ email, name });
  }

  function signInAs(account: LoginRequest) {
    signingAs = account.email;
    void signIn(account);
  }
</script>

<main class="page">
  <section class="card login" aria-labelledby="login-title">
    <div class="brand">
      <span class="mark" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
          <path d="M14 3v5h5M9 13h6M9 17h4" />
        </svg>
      </span>
      <span>
        <span class="product">Contract OCR</span>
        <span class="muted small">Pemeriksaan kontrak PKWT</span>
      </span>
    </div>

    <div class="heading">
      <h1 id="login-title">Masuk</h1>
      <p class="muted">Gunakan akun kantor Blibli untuk melanjutkan.</p>
    </div>

    {#if error}
      <Banner tone="danger">{errorLabel(error)}</Banner>
    {:else if notice}
      <!-- a session that ended is news; anything else (backend down) is a problem -->
      <Banner tone={notice === 'unauthenticated' ? 'info' : 'danger'}>{errorLabel(notice)}</Banner>
    {/if}

    <form onsubmit={submit} novalidate>
      <label class="field">
        <span class="label">Email kantor</span>
        <input
          type="email"
          name="email"
          autocomplete="email"
          inputmode="email"
          placeholder="nama@gdn-commerce.com"
          maxlength="255"
          required
          bind:value={email}
          disabled={busy}
        />
      </label>
      <label class="field">
        <span class="label">Nama lengkap</span>
        <input
          type="text"
          name="name"
          autocomplete="name"
          placeholder="Nama sesuai akun kantor"
          maxlength="255"
          required
          bind:value={name}
          disabled={busy}
        />
      </label>
      <button class="btn btn-primary submit" type="submit" disabled={busy || !email.trim() || !name.trim()}>
        {busy && !signingAs ? 'Memproses…' : 'Masuk'}
      </button>
    </form>

    {#if quick}
      <div class="quick">
        <p class="divider"><span>atau</span></p>
        <button
          class="btn btn-outline toggle"
          type="button"
          aria-expanded={accountsOpen}
          aria-controls="test-accounts"
          disabled={busy}
          onclick={() => (accountsOpen = !accountsOpen)}
        >
          Masuk cepat dengan akun uji
          <svg class="chevron" class:open={accountsOpen} viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
            <path fill="none" stroke="currentColor" stroke-width="2" d="m6 9 6 6 6-6" />
          </svg>
        </button>
        {#if accountsOpen}
          <ul id="test-accounts" class="accounts" aria-label="Akun uji">
            {#each TEST_ACCOUNTS as account (account.email)}
              <li>
                <button class="account" type="button" disabled={busy} onclick={() => signInAs(account)}>
                  <span class="avatar" aria-hidden="true">{initials(account.name)}</span>
                  <span class="who">
                    <span class="name">{account.name}</span>
                    <span class="email">{account.email}</span>
                  </span>
                  <span class="go">{signingAs === account.email ? 'Memproses…' : 'Masuk'}</span>
                </button>
              </li>
            {/each}
          </ul>
          <p class="muted small">Akun fiktif untuk menguji beberapa pengguna. Hanya tampil saat dijalankan lokal (<code>bun run dev</code>).</p>
        {/if}
      </div>
    {/if}

    <Banner tone="warning">
      <strong>Login tiruan untuk pengujian.</strong> Tidak ada kata sandi yang diminta atau diperiksa. Nanti diganti
      Apps Gateway (SSO kantor). Email yang pertama kali masuk dicatat di tabel User.
    </Banner>
  </section>
  <p class="muted small footer">© Blibli · Hanya untuk pengguna internal</p>
</main>

<style>
  .page {
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: var(--blue-space-4);
    padding: var(--blue-space-6) var(--blue-space-4);
  }
  .login {
    width: 100%;
    max-width: 420px;
    display: flex;
    flex-direction: column;
    gap: var(--blue-space-5);
    padding: var(--blue-space-6);
    border-top: 4px solid var(--blue-brand);
  }
  .brand {
    display: flex;
    align-items: center;
    gap: var(--blue-space-3);
  }
  .mark {
    display: grid;
    place-items: center;
    width: 40px;
    height: 40px;
    border-radius: var(--blue-radius);
    background: var(--blue-brand);
    color: var(--blue-text-inverse);
  }
  .product {
    display: block;
    font-weight: 700;
    font-size: 16px;
    line-height: 20px;
  }
  .small {
    display: block;
    font-size: 12px;
  }
  .heading {
    display: flex;
    flex-direction: column;
    gap: var(--blue-space-1);
  }
  h1 {
    font-size: 24px;
    line-height: 32px;
    font-weight: 700;
  }
  form {
    display: flex;
    flex-direction: column;
    gap: var(--blue-space-4);
  }
  .field {
    display: flex;
    flex-direction: column;
    gap: var(--blue-space-1);
  }
  .label {
    font-size: 12px;
    font-weight: 700;
    color: var(--blue-text);
  }
  input {
    height: 40px;
    padding: 0 var(--blue-space-3);
    border: 1px solid var(--blue-border-strong);
    border-radius: var(--blue-radius);
    background: var(--blue-surface);
    color: var(--blue-text);
    font: 14px/1.5 var(--blue-font);
  }
  input::placeholder {
    color: var(--blue-text-muted);
  }
  input:focus {
    outline: none;
    border-color: var(--blue-brand);
    box-shadow: 0 0 0 3px var(--blue-brand-subtle);
  }
  input:disabled {
    background: var(--blue-surface-muted);
  }
  .submit {
    min-height: 40px;
    width: 100%;
  }
  .footer {
    text-align: center;
  }
  .quick {
    display: flex;
    flex-direction: column;
    gap: var(--blue-space-3);
  }
  .divider {
    display: flex;
    align-items: center;
    gap: var(--blue-space-3);
    font-size: 12px;
    color: var(--blue-text-muted);
  }
  .divider::before,
  .divider::after {
    content: '';
    flex: 1;
    border-top: 1px solid var(--blue-border);
  }
  .toggle {
    min-height: 40px;
    width: 100%;
    gap: var(--blue-space-2);
  }
  .chevron {
    transition: transform 0.15s;
  }
  .chevron.open {
    transform: rotate(180deg);
  }
  .accounts {
    list-style: none;
    margin: 0;
    padding: 0;
    border: 1px solid var(--blue-border);
    border-radius: var(--blue-radius);
    overflow: hidden;
  }
  .accounts li + li {
    border-top: 1px solid var(--blue-border);
  }
  .account {
    display: flex;
    align-items: center;
    gap: var(--blue-space-3);
    width: 100%;
    padding: var(--blue-space-2) var(--blue-space-3);
    border: none;
    background: var(--blue-surface);
    color: var(--blue-text);
    font: inherit;
    text-align: left;
    cursor: pointer;
  }
  .account:hover:not(:disabled),
  .account:focus-visible {
    background: var(--blue-surface-muted);
  }
  .account:disabled {
    cursor: progress;
  }
  .avatar {
    display: grid;
    flex: none;
    place-items: center;
    width: 32px;
    height: 32px;
    border-radius: 50%;
    background: var(--blue-brand-subtle);
    color: var(--blue-brand-pressed);
    font-size: 12px;
    font-weight: 700;
  }
  .who {
    flex: 1;
    min-width: 0;
  }
  .name {
    display: block;
    font-size: 14px;
    font-weight: 600;
  }
  .email {
    display: block;
    font-size: 12px;
    color: var(--blue-text-muted);
    overflow-wrap: anywhere;
  }
  .go {
    flex: none;
    font-size: 13px;
    font-weight: 600;
    color: var(--blue-link);
  }
  @media (max-width: 480px) {
    .login {
      padding: var(--blue-space-5) var(--blue-space-4);
    }
  }
</style>
