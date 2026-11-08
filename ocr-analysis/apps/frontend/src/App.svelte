<script lang="ts">
  import type { Health, User } from '@contract-ocr/shared-types';
  import { onMount } from 'svelte';
  import Banner from './components/Banner.svelte';
  import BrowsePage from './pages/BrowsePage.svelte';
  import HistoryPage from './pages/HistoryPage.svelte';
  import JobPage from './pages/JobPage.svelte';
  import LoginPage from './pages/LoginPage.svelte';
  import { SIGNED_OUT_EVENT, api, failureCode } from './services/api.ts';
  import { errorLabel } from './utils/labels.ts';
  import { folderHref, historyHref, parseRoute } from './utils/routes.ts';

  const HEALTH_READY_MS = 15_000;
  const HEALTH_WAITING_MS = 3_000;

  let route = $state(parseRoute(location.hash));
  let health = $state<Health | null>(null);
  let offline = $state(false);
  // undefined while asking the backend; null when nobody is signed in
  let user = $state<User | null | undefined>(undefined);
  let loginNotice = $state<string | null>(null);
  let signingOut = $state(false);
  const signedIn = $derived(user != null);
  // The folder and the history list last open, so each tab returns to where it was.
  let lastFolder = $state('');
  let lastHistory = $state('');
  $effect(() => {
    if (route.page === 'browse') lastFolder = route.path;
    if (route.page === 'history') lastHistory = route.query;
  });

  onMount(() => {
    const onHash = () => (route = parseRoute(location.hash));
    addEventListener('hashchange', onHash);
    const onSignedOut = () => {
      if (user) loginNotice = 'unauthenticated';
      user = null;
    };
    addEventListener(SIGNED_OUT_EVENT, onSignedOut);
    api.me().then(
      (session) => (user = session.user),
      (error: unknown) => {
        const code = failureCode(error);
        loginNotice = code === 'unauthenticated' ? null : code;
        user = null;
      },
    );
    return () => {
      removeEventListener('hashchange', onHash);
      removeEventListener(SIGNED_OUT_EVENT, onSignedOut);
    };
  });

  // The backend's state, polled only while someone is signed in (every other route answers 401 before that).
  $effect(() => {
    if (!signedIn) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let alive = true;
    const poll = async () => {
      try {
        health = await api.health();
        offline = false;
      } catch {
        offline = true;
      }
      if (alive) timer = setTimeout(poll, !offline && health?.extractor.state === 'ready' ? HEALTH_READY_MS : HEALTH_WAITING_MS);
    };
    void poll();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  });

  function signedInAs(next: User) {
    loginNotice = null;
    user = next;
  }

  async function signOut() {
    signingOut = true;
    try {
      await api.logout();
    } catch {
      // backend unreachable: its in-memory session ends on its own (expiry or restart)
    } finally {
      signingOut = false;
      health = null;
      user = null;
    }
  }

  const routeKey = $derived(
    route.page === 'job' ? `job:${route.id}` : route.page === 'history' ? 'history' : `folder:${route.path}`,
  );
  const section = $derived(route.page === 'browse' ? 'folder' : 'history'); // a run belongs to the history
  const rootName = $derived(health?.rootName ?? 'Folder');
  const extractor = $derived(health?.extractor ?? null);
</script>

{#if user === null}
  <LoginPage onSignedIn={signedInAs} notice={loginNotice} />
{:else if user}
<header class="topbar">
  <div class="topbar-inner">
    <a class="brand" href="#/">
      <span class="mark" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
          <path d="M14 3v5h5M9 13h6M9 17h4" />
        </svg>
      </span>
      <span>
        <span class="title">Contract OCR</span>
        <span class="subtitle">Pemeriksaan kontrak PKWT</span>
      </span>
    </a>
    <nav class="tabs" aria-label="Halaman">
      <a href={folderHref(lastFolder)} class:active={section === 'folder'} aria-current={route.page === 'browse' ? 'page' : undefined}>
        Folder
      </a>
      <a href={historyHref(lastHistory)} class:active={section === 'history'} aria-current={route.page === 'history' ? 'page' : undefined}>
        Riwayat OCR
      </a>
    </nav>
    <span class="status" class:ok={!offline && extractor?.state === 'ready'} class:bad={offline || extractor?.state === 'unavailable'}>
      <span class="dot" aria-hidden="true"></span>
      {#if offline}
        Backend mati
      {:else if !extractor || extractor.state === 'starting'}
        OCR disiapkan…
      {:else if extractor.state === 'ready'}
        OCR siap · {extractor.workers} pembaca{#if health?.claude === 'on'} · dibantu Claude{/if}
      {:else}
        OCR tidak tersedia
      {/if}
    </span>
    <div class="account">
      <span class="who" title={user.email}>
        <span class="avatar" aria-hidden="true">{user.name.charAt(0).toUpperCase()}</span>
        <span class="name">{user.name}</span>
      </span>
      <button class="btn btn-ghost btn-sm" type="button" onclick={signOut} disabled={signingOut}>Keluar</button>
    </div>
  </div>
</header>

<main class="container">
  {#if offline}
    <Banner tone="danger">{errorLabel('network')}</Banner>
  {:else if extractor?.state === 'unavailable'}
    <Banner tone="danger">
      Pembaca OCR tidak bisa dijalankan ({errorLabel(extractor.error ?? 'extractor_unavailable')}). Periksa
      <span class="mono">TESSERACT_CMD</span> dan <span class="mono">EXTRACTOR_PYTHON</span> di
      <span class="mono">apps/backend/.env</span>, lalu jalankan ulang backend.
    </Banner>
  {/if}

  {#key routeKey}
    {#if route.page === 'job'}
      <JobPage id={route.id} {rootName} userId={user.id} />
    {:else if route.page === 'history'}
      <HistoryPage {rootName} query={route.query} />
    {:else}
      <BrowsePage path={route.path} {rootName} {extractor} claude={health?.claude ?? null} />
    {/if}
  {/key}
</main>
{/if}

<style>
  .topbar {
    background: var(--blue-surface);
    border-bottom: 1px solid var(--blue-border);
    position: sticky;
    top: 0;
    z-index: 10;
  }
  .topbar-inner,
  .container {
    max-width: 1280px;
    margin: 0 auto;
    padding: 0 var(--blue-space-5);
  }
  .topbar-inner {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--blue-space-4);
    height: 60px;
  }
  .brand {
    display: flex;
    align-items: center;
    gap: var(--blue-space-3);
    color: var(--blue-text);
  }
  .brand:hover {
    text-decoration: none;
  }
  .mark {
    display: grid;
    place-items: center;
    width: 32px;
    height: 32px;
    border-radius: var(--blue-radius);
    background: var(--blue-brand);
    color: var(--blue-text-inverse);
  }
  .title {
    display: block;
    font-weight: 700;
    font-size: 16px;
    line-height: 20px;
  }
  .subtitle {
    display: block;
    font-size: 12px;
    color: var(--blue-text-muted);
  }
  .status {
    display: inline-flex;
    align-items: center;
    gap: var(--blue-space-2);
    font-size: 12px;
    font-weight: 600;
    color: var(--blue-text-muted);
  }
  .status {
    margin-left: auto;
  }
  .tabs {
    display: flex;
    align-self: stretch;
    gap: var(--blue-space-1);
    margin-left: var(--blue-space-4);
  }
  .tabs a {
    display: flex;
    align-items: center;
    padding: 0 var(--blue-space-3);
    border-bottom: 3px solid transparent;
    color: var(--blue-text-muted);
    font-size: 14px;
    font-weight: 600;
    white-space: nowrap;
  }
  .tabs a:hover {
    color: var(--blue-text);
    text-decoration: none;
  }
  .tabs a.active {
    border-bottom-color: var(--blue-brand);
    color: var(--blue-link);
  }
  .account {
    display: flex;
    align-items: center;
    gap: var(--blue-space-2);
    flex: 0 1 auto;
    min-width: max-content; /* the status text wraps first; Keluar never leaves the screen */
    padding-left: var(--blue-space-4);
    border-left: 1px solid var(--blue-border);
  }
  .who {
    display: inline-flex;
    align-items: center;
    gap: var(--blue-space-2);
    min-width: 28px; /* the avatar; only the name shrinks */
    font-size: 12px;
    font-weight: 600;
  }
  .avatar {
    flex: none;
    display: grid;
    place-items: center;
    width: 28px;
    height: 28px;
    border-radius: 50%;
    background: var(--blue-brand-subtle);
    color: var(--blue-link);
    font-weight: 700;
  }
  .name {
    max-width: 180px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .status .dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--blue-warning);
  }
  .status.ok .dot {
    background: var(--blue-success);
  }
  .status.bad .dot {
    background: var(--blue-danger);
  }
  .container {
    display: flex;
    flex-direction: column;
    gap: var(--blue-space-4);
    padding-top: var(--blue-space-5);
    padding-bottom: var(--blue-space-6);
  }
  /* Too narrow for the tabs beside the brand: they take a row of their own under it. */
  @media (max-width: 900px) {
    .topbar-inner {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr) auto;
      grid-template-areas: 'brand status account' 'tabs tabs tabs';
      height: auto;
      row-gap: 0;
    }
    .brand {
      grid-area: brand;
      min-height: 60px;
    }
    .status {
      grid-area: status;
      justify-self: end;
    }
    .account {
      grid-area: account;
    }
    .tabs {
      grid-area: tabs;
      height: 44px;
      margin: 0;
      border-top: 1px solid var(--blue-border);
    }
    .tabs a {
      flex: 1;
      justify-content: center;
    }
  }
  /* A phone: the brand and the account on top, the backend's state under them, then the tabs. */
  @media (max-width: 640px) {
    .topbar-inner {
      grid-template-columns: minmax(0, 1fr) auto;
      grid-template-areas: 'brand account' 'status status' 'tabs tabs';
    }
    .brand {
      min-height: 52px;
    }
    .status {
      justify-self: start;
      margin-left: 0;
      padding-bottom: var(--blue-space-2);
    }
  }
  @media (max-width: 640px) {
    .topbar-inner,
    .container {
      padding-left: var(--blue-space-4);
      padding-right: var(--blue-space-4);
    }
    .subtitle,
    .name {
      display: none;
    }
    .account {
      padding-left: var(--blue-space-2);
    }
  }
</style>
