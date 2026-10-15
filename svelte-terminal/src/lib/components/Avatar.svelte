<!-- src/lib/components/Avatar.svelte -->
<!-- A round profile picture. Without a picture (or if it fails to load) it shows the character's first letter. -->
<script lang="ts">
  interface Props {
    src?: string | null;
    name: string;
    size?: 'sm' | 'md' | 'lg';
  }

  let { src = null, name, size = 'md' }: Props = $props();

  const dimensions = $derived(
    size === 'lg' ? 'w-24 h-24 text-3xl' : size === 'sm' ? 'w-6 h-6 text-[10px]' : 'w-8 h-8 text-xs'
  );

  // The same name always gets the same colour behind its letter.
  const hue = $derived([...name].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7));

  // Remember which address failed, so changing the picture tries again.
  let brokenSrc = $state<string | null>(null);
  const showImage = $derived(!!src && brokenSrc !== src);
</script>

{#if showImage}
  <img
    {src}
    alt={name}
    onerror={() => (brokenSrc = src)}
    class="{dimensions} rounded-full object-cover shrink-0 border border-slate-700 bg-slate-900"
  />
{:else}
  <div
    class="{dimensions} rounded-full shrink-0 flex items-center justify-center font-mono font-semibold text-white/90 border border-slate-700"
    style="background: hsl({hue} 45% 32%)"
    role="img"
    aria-label={name}
  >
    {(name.trim()[0] ?? '?').toUpperCase()}
  </div>
{/if}
