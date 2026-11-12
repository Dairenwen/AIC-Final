<script setup lang="ts">
import { computed } from 'vue'

import { glyphs, type GlyphName } from './glyphs'

const props = withDefaults(defineProps<{ name: GlyphName; size?: number | string }>(), { size: 18 })

// pathLength="1" normalises every primitive, so the draw-on animation in
// motion.css works with one dash value for all icons.
const markup = computed(() => glyphs[props.name].join('').replace(/\/>/g, ' pathLength="1"/>'))
</script>

<template>
  <!-- The markup is a compile-time constant from glyphs.ts, never user input. -->
  <svg
    class="zy-icon"
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.5"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    focusable="false"
    v-html="markup"
  />
</template>
