<script lang="ts">
  import type { User } from '@contract-ocr/shared-types';
  import { onMount } from 'svelte';
  import Banner from '../components/Banner.svelte';

  // A signed-in user an admin has not activated (a new user, or one deactivated later). The backend answers every
  // other route with 403 `account_inactive`, so this page is all there is until an admin activates the account; it
  // asks again every half minute, so the website opens by itself once that happens.

  const CHECK_MS = 30_000;

  let {
    user,
    oncheck,
    onsignout,
  }: { user: User; oncheck: () => Promise<boolean>; onsignout: () => void } = $props();

  let checking = $state(false);
  let stillWaiting = $state(false); // "Periksa lagi" was pressed and the account is still inactive

  async function check(manual: boolean) {
    if (checking) return;
    checking = true;
    try {
      const active = await oncheck();
      if (manual) stillWaiting = !active;
    } finally {
      checking = false;
    }
  }

  onMount(() => {
    const timer = setInterval(() => void check(false), CHECK_MS);
    return () => clearInterval(timer);
  });
</script>

<main class="page">
  <section class="card waiting" aria-labelledby="waiting-title">
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

    <div class="icon" aria-hidden="true">
      <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="2">
        <circle cx="12" cy="12" r="9" />
        <path d="M12 7v5l3 2" />
      </svg>
    </div>

    <div class="heading">
      <h1 id="waiting-title">Menunggu aktivasi admin</h1>
      <p class="muted">
        Akun Anda sudah tercatat, tetapi belum aktif. Minta admin Contract OCR mengaktifkannya di halaman User.
        Halaman ini memeriksa lagi otomatis setiap 30 detik.
      </p>
    </div>

    <dl class="account">
      <div>
        <dt>Nama</dt>
        <dd>{user.name}</dd>
      </div>
      <div>
        <dt>Email</dt>
        <dd>{user.email}</dd>
      </div>
    </dl>

    {#if stillWaiting}
      <Banner tone="info">Akun Anda masih belum aktif.</Banner>
    {/if}

    <div class="actions">
      <button class="btn btn-primary" type="button" onclick={() => check(true)} disabled={checking}>
        {checking ? 'Memeriksa…' : 'Periksa lagi'}
      </button>
      <button class="btn btn-ghost" type="button" onclick={onsignout}>Keluar</button>
    </div>
  </section>
  <p class="muted small footer">© Blibli · Hanya untuk user internal</p>
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
  .waiting {
    width: 100%;
    max-width: 460px;
    display: flex;
    flex-direction: column;
    gap: var(--blue-space-5);
    padding: var(--blue-space-6);
    border-top: 4px solid var(--blue-warning);
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
  .icon {
    display: grid;
    place-items: center;
    width: 52px;
    height: 52px;
    border-radius: 50%;
    background: var(--blue-warning-subtle);
    color: var(--blue-warning-text);
  }
  .heading {
    display: flex;
    flex-direction: column;
    gap: var(--blue-space-2);
  }
  h1 {
    font-size: 22px;
    line-height: 30px;
    font-weight: 700;
  }
  .account {
    display: flex;
    flex-direction: column;
    gap: var(--blue-space-2);
    margin: 0;
    padding: var(--blue-space-3) var(--blue-space-4);
    border: 1px solid var(--blue-border);
    border-radius: var(--blue-radius);
    background: var(--blue-surface-muted);
  }
  .account div {
    display: grid;
    grid-template-columns: 64px minmax(0, 1fr);
    gap: var(--blue-space-3);
  }
  dt {
    font-size: 12px;
    font-weight: 700;
    color: var(--blue-text-muted);
  }
  dd {
    margin: 0;
    overflow-wrap: anywhere;
  }
  .actions {
    display: flex;
    gap: var(--blue-space-2);
  }
  .actions .btn-primary {
    flex: 1;
    min-height: 40px;
  }
  .footer {
    text-align: center;
  }
  @media (max-width: 480px) {
    .waiting {
      padding: var(--blue-space-5) var(--blue-space-4);
    }
  }
</style>
