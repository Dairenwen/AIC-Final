<script setup lang="ts">
import ZyIcon from '@/components/icons/ZyIcon.vue'

defineProps<{ message: string }>()
</script>

<template>
  <div class="empty-notice fade-in">
    <ZyIcon class="zy-icon--draw" name="inbox" :size="26" />
    <p>{{ message }}</p>
  </div>
</template>
