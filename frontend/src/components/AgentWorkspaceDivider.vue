<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'

const divider = ref<HTMLElement | null>(null)
const position = ref<number | null>(null)
let workspace: HTMLElement | null = null
let observer: ResizeObserver | null = null
let dragging = false
let pointerId: number | null = null

function limits() {
  const width = workspace?.clientWidth ?? 0
  const maximum = Math.max(0, width - 328)
  return { min: Math.min(240, maximum), max: maximum }
}

function setPosition(value: number) {
  if (!workspace) return
  const { min, max } = limits()
  position.value = Math.round(Math.min(max, Math.max(min, value)))
  workspace.style.setProperty('--agent-pane-left', `${position.value}px`)
}

function currentPosition() {
  return position.value ?? (divider.value?.previousElementSibling as HTMLElement | null)?.getBoundingClientRect().width ?? 0
}

function startDrag(event: PointerEvent) {
  if (event.button !== 0 || !workspace || !divider.value) return
  dragging = true
  pointerId = event.pointerId
  divider.value.setPointerCapture(event.pointerId)
  workspace.classList.add('agent-workspace-dragging')
  event.preventDefault()
}

function moveDrag(event: PointerEvent) {
  if (!dragging || event.pointerId !== pointerId || !workspace) return
  setPosition(event.clientX - workspace.getBoundingClientRect().left)
}

function stopDrag(event: PointerEvent) {
  if (event.pointerId !== pointerId) return
  dragging = false
  pointerId = null
  workspace?.classList.remove('agent-workspace-dragging')
  if (divider.value?.hasPointerCapture(event.pointerId)) divider.value.releasePointerCapture(event.pointerId)
}

function loseCapture() {
  dragging = false
  pointerId = null
  workspace?.classList.remove('agent-workspace-dragging')
}

function resetPosition() {
  position.value = null
  workspace?.style.removeProperty('--agent-pane-left')
}

function handleKey(event: KeyboardEvent) {
  if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
    event.preventDefault()
    setPosition(currentPosition() + (event.key === 'ArrowRight' ? 20 : -20))
  } else if (event.key === 'Home' || event.key === 'End') {
    event.preventDefault()
    const { min, max } = limits()
    setPosition(event.key === 'Home' ? min : max)
  }
}

onMounted(() => {
  workspace = divider.value?.parentElement ?? null
  if (workspace && typeof ResizeObserver !== 'undefined') {
    observer = new ResizeObserver(() => {
      if (position.value !== null) setPosition(position.value)
    })
    observer.observe(workspace)
  }
})

onBeforeUnmount(() => {
  observer?.disconnect()
  workspace?.classList.remove('agent-workspace-dragging')
  workspace?.style.removeProperty('--agent-pane-left')
})
</script>

<template>
  <div
    ref="divider"
    class="agent-workspace-divider"
    role="separator"
    aria-label="调整工作区宽度"
    aria-orientation="vertical"
    tabindex="0"
    @pointerdown="startDrag"
    @pointermove="moveDrag"
    @pointerup="stopDrag"
    @pointercancel="stopDrag"
    @lostpointercapture="loseCapture"
    @dblclick="resetPosition"
    @keydown="handleKey"
  ></div>
</template>
