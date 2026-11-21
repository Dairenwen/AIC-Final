<script setup lang="ts">
// The decorative all-caps strap line that used to sit above every title was
// removed; a page needs a title and a description, not a third label.
defineProps<{
  title: string
  description?: string
}>()
</script>

<template>
  <header class="page-header">
    <div>
      <h1>{{ title }}</h1>
      <p v-if="description" class="page-description">{{ description }}</p>
    </div>
    <div class="page-actions">
      <slot />
    </div>
  </header>
</template>
