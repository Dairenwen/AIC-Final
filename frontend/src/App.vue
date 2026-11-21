<script setup lang="ts">
import { RouterView, useRoute } from 'vue-router'

import { authState } from '@/auth/session'
import AppShell from '@/components/AppShell.vue'

const route = useRoute()
</script>

<template>
  <RouterView v-if="route.meta.public" />
  <AppShell v-else-if="authState.initialized && authState.user" />
</template>
