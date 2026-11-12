<script setup lang="ts">
withDefaults(defineProps<{ size?: number | string }>(), { size: 28 })
</script>

<template>
  <!-- An ink block carrying one continuous brush stroke, closed by a seal dot. -->
  <svg class="brand-glyph" :width="size" :height="size" viewBox="0 0 32 32" aria-hidden="true" focusable="false">
    <rect class="brand-glyph__block" width="32" height="32" rx="8" />
    <path
      class="brand-glyph__stroke"
      d="M9.5 10.5h13L9.5 21.5h9"
      fill="none"
      stroke-width="2.4"
      stroke-linecap="round"
      stroke-linejoin="round"
      pathLength="1"
    />
    <circle class="brand-glyph__seal" cx="22.5" cy="21.5" r="1.9" />
  </svg>
</template>
