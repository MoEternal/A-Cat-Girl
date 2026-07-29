<script setup lang="ts">
import { computed, ref } from 'vue'
import { Eye, EyeOff, KeyRound, LogIn, ShieldCheck, UserRound } from '@lucide/vue'
import { api, json } from '../api'
import type { AuthStatus } from '../types'

interface ThemeStage {
  name: string
  accent: string
}

const props = defineProps<{
  setupRequired: boolean
  themeStages: ThemeStage[]
  themeStage: number
}>()
const emit = defineEmits<{
  authenticated: [status: AuthStatus]
  themeChange: [stage: number]
}>()

const username = ref('')
const password = ref('')
const confirmation = ref('')
const passwordVisible = ref(false)
const busy = ref(false)
const error = ref('')
const heading = computed(() => (props.setupRequired ? '创建管理员账号' : '登录管理控制台'))
const currentTheme = computed(() => props.themeStages[props.themeStage] ?? props.themeStages[0])

function updateTheme(event: Event) {
  emit('themeChange', Number((event.target as HTMLInputElement).value))
}

async function submit() {
  if (busy.value) return
  error.value = ''
  if (props.setupRequired && password.value !== confirmation.value) {
    error.value = '两次输入的密码不一致'
    return
  }
  busy.value = true
  try {
    const endpoint = props.setupRequired ? '/api/auth/setup' : '/api/auth/login'
    const status = await api<AuthStatus>(endpoint, json('POST', {
      username: username.value,
      password: password.value,
    }))
    password.value = ''
    confirmation.value = ''
    emit('authenticated', status)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '登录失败'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <main class="auth-screen">
    <section class="auth-panel" aria-labelledby="auth-heading">
      <div class="auth-brand">
        <span class="auth-brand-mark"><img src="/catgirl-logo.png" alt="" /></span>
        <div class="auth-brand-copy"><strong>一只猫娘</strong><span>管理控制台</span></div>
        <div class="auth-theme-control" :title="`主题色：${currentTheme?.name ?? ''}`">
          <span class="visually-hidden">主题色：{{ currentTheme?.name }}</span>
          <div class="theme-stage-track">
            <span class="theme-stage-line" aria-hidden="true" />
            <span class="theme-stage-dots" aria-hidden="true">
              <i v-for="(stage, index) in themeStages" :key="stage.name" :style="{ '--stage-left': `${index * 100 / (themeStages.length - 1)}%`, '--stage-color': stage.accent }" />
            </span>
            <input
              class="theme-stage-slider"
              type="range"
              min="0"
              :max="themeStages.length - 1"
              step="1"
              :value="themeStage"
              :aria-label="`登录页主题色：${currentTheme?.name ?? ''}`"
              @input="updateTheme"
            />
          </div>
        </div>
      </div>

      <div class="auth-heading">
        <span class="auth-heading-icon"><ShieldCheck v-if="setupRequired" :size="20" /><LogIn v-else :size="20" /></span>
        <h1 id="auth-heading">{{ heading }}</h1>
      </div>

      <form class="auth-form" @submit.prevent="submit">
        <label class="auth-field">
          <span>用户名</span>
          <span class="auth-input-wrap">
            <UserRound :size="16" />
            <input v-model="username" name="username" autocomplete="username" minlength="1" maxlength="80" required autofocus />
          </span>
        </label>

        <label class="auth-field">
          <span>密码</span>
          <span class="auth-input-wrap">
            <KeyRound :size="16" />
            <input v-model="password" name="password" :type="passwordVisible ? 'text' : 'password'" :autocomplete="setupRequired ? 'new-password' : 'current-password'" minlength="8" maxlength="256" required />
            <button class="auth-password-toggle" type="button" :title="passwordVisible ? '隐藏密码' : '显示密码'" :aria-label="passwordVisible ? '隐藏密码' : '显示密码'" @click="passwordVisible = !passwordVisible">
              <EyeOff v-if="passwordVisible" :size="16" /><Eye v-else :size="16" />
            </button>
          </span>
        </label>

        <label v-if="setupRequired" class="auth-field">
          <span>确认密码</span>
          <span class="auth-input-wrap">
            <KeyRound :size="16" />
            <input v-model="confirmation" name="password-confirmation" :type="passwordVisible ? 'text' : 'password'" autocomplete="new-password" minlength="8" maxlength="256" required />
          </span>
        </label>

        <p class="auth-error" role="alert" aria-live="polite">{{ error }}</p>
        <button class="button primary auth-submit" type="submit" :disabled="busy">
          <ShieldCheck v-if="setupRequired" :size="16" /><LogIn v-else :size="16" />
          {{ busy ? '请稍候' : (setupRequired ? '创建并进入' : '登录') }}
        </button>
      </form>
    </section>
  </main>
</template>
