<script>
  // Chat with the task-understanding agent. The full history is sent each
  // turn (the backend agent is stateless); a ready proposal can be run.
  import { app } from '../lib/store.svelte.js';
  import { api, isDemo } from '../lib/api.js';
  import { startRun } from '../lib/runs.js';

  const CHIPS = ['Rush hour at Silk Board Junction, Bengaluru at 9am',
                 'Close lane 0 of Hosur Road',
                 'Night traffic at Silk Board Junction, Bengaluru at 11pm'];
  const SLOW_MS = 6000; // after this, explain that the LLM may be starting up
  let draft = $state('');
  let slow = $state(false);
  let log;

  /** Minimal, safe markdown: escape HTML then render **bold** and *italic*. */
  function md(text) {
    const esc = text.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
    return esc.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/\*(.+?)\*/g, '<i>$1</i>');
  }

  /** Send one message and apply the agent's extracted parameters. */
  async function send(text) {
    const content = text.trim();
    if (!content || app.chatBusy) return;
    app.chat.push({ role: 'user', content });
    draft = '';
    if (isDemo()) {
      app.chat.push({ role: 'assistant', content: 'The simulation backend is not connected to this deployment, so I can only replay the recorded demo run. Run the backend (see README) to simulate new queries.' });
      return;
    }
    app.chatBusy = true;
    const timer = setTimeout(() => { slow = true; }, SLOW_MS);
    try {
      const history = app.chat.map(({ role, content }) => ({ role, content }));
      const r = await api.chat(history, { ...app.params, ...app.edits });
      app.chat.push({ role: 'assistant', content: r.reply });
      app.proposal = r.ready ? r.params : null;
    } catch (err) {
      app.chat.push({ role: 'assistant', content: `Sorry, the agent failed: ${err.message}` });
    } finally {
      clearTimeout(timer);
      slow = false;
      app.chatBusy = false;
    }
  }

  /** Copy the proposal into the form + edits, then start the run. */
  function runProposal() {
    const p = app.proposal;
    const { close_edges, close_roads, close_lanes, ...params } = p;
    app.params = { ...app.params, ...params };
    app.edits = { close_edges: [...app.edits.close_edges, ...close_edges],
                  close_roads, close_lanes };
    startRun({ ...p, close_edges: app.edits.close_edges });
    app.proposal = null;
  }

  $effect(() => {
    app.chat.length;
    log?.scrollTo({ top: log.scrollHeight, behavior: 'smooth' });
  });
</script>

<section class="panel chat">
  <div class="panel-head"><span>Chat with agent</span><span class="muted">Sim agent</span></div>
  <div class="log" bind:this={log}>
    {#each app.chat as m}
      <div class="msg {m.role}">{@html md(m.content)}</div>
    {/each}
    {#if app.chat.length === 1}
      <div class="chips">
        {#each CHIPS as c}<button class="chip" onclick={() => send(c)}>{c}</button>{/each}
      </div>
    {/if}
    {#if app.chatBusy}
      <div class="msg assistant typing">
        {slow ? 'Waking up the language model. After a quiet spell the first reply can take up to 2 minutes…' : '…'}
      </div>
    {/if}
    {#if app.proposal}
      <button class="btn primary run" onclick={runProposal}>Run this simulation</button>
    {/if}
  </div>
  <form class="composer" onsubmit={(e) => { e.preventDefault(); send(draft); }}>
    <input class="chat-input" placeholder="Type a message…" bind:value={draft} maxlength="2000" />
    <button class="send" type="submit" aria-label="Send" disabled={!draft.trim()}>
      <svg viewBox="0 0 24 24" width="18" height="18"><path d="M5 12h13M13 6l6 6-6 6" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>
    </button>
  </form>
</section>

<style>
  .chat { flex: 1; min-height: 260px; display: flex; flex-direction: column; }
  .log { flex: 1; overflow-y: auto; padding: 14px 16px; display: flex; flex-direction: column; gap: 10px; }
  .msg { padding: 9px 12px; border-radius: 10px; max-width: 92%; line-height: 1.45; font-size: 13.5px; }
  .msg.assistant { background: var(--panel-2); align-self: flex-start; }
  .msg.user { background: rgba(245, 165, 36, 0.14); color: #ffd99a; align-self: flex-end; }
  .typing { color: var(--muted); }
  .chips { display: flex; flex-wrap: wrap; gap: 8px; }
  .chip { background: none; border: 1px solid var(--line-strong); color: var(--text-2); border-radius: 999px;
          padding: 6px 12px; font-size: 12.5px; text-align: left; }
  .chip:hover { border-color: var(--accent); color: var(--text); }
  .run { align-self: flex-start; }
  .composer { display: flex; gap: 10px; padding: 12px 14px; border-top: 1px solid var(--line); }
  .composer input { flex: 1; min-width: 0; border-radius: 999px; padding: 11px 16px; }
  .send { width: 42px; height: 42px; flex: none; border-radius: 50%; border: 0; background: var(--accent);
          color: #141414; display: grid; place-items: center; }
  .send:disabled { opacity: 0.5; }
</style>
