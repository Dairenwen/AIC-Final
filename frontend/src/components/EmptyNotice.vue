<script setup lang="ts">
import { Inbox } from 'lucide-vue-next'

defineProps<{ message: string }>()
</script>

<template>
  <div class="empty-notice">
    <Inbox :size="22" />
    <p>{{ message }}</p>
  </div>
</template>

