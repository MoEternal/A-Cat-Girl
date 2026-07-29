import { createRouter, createWebHashHistory } from 'vue-router'

import CharactersView from './views/CharactersView.vue'
import DashboardView from './views/DashboardView.vue'
import PromptsView from './views/PromptsView.vue'
import ProvidersView from './views/ProvidersView.vue'
import PresetsView from './views/PresetsView.vue'
import PluginsView from './views/PluginsView.vue'
import QqConnectionView from './views/QqConnectionView.vue'
import ChatHistoryView from './views/ChatHistoryView.vue'
import UserPersonasView from './views/UserPersonasView.vue'
import WorldBooksView from './views/WorldBooksView.vue'

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', name: 'dashboard', component: DashboardView, meta: { title: '配置总览' } },
    { path: '/presets', name: 'presets', component: PresetsView, meta: { title: '预设配置' } },
    { path: '/providers', name: 'providers', component: ProvidersView, meta: { title: 'API配置' } },
    { path: '/prompts', name: 'prompts', component: PromptsView, meta: { title: '提示词编辑' } },
    { path: '/user-personas', name: 'user-personas', component: UserPersonasView, meta: { title: '用户人设' } },
    { path: '/characters', name: 'characters', component: CharactersView, meta: { title: '角色人设' } },
    { path: '/plugins', name: 'plugins', component: PluginsView, meta: { title: '插件' } },
    { path: '/qq-connection', name: 'qq-connection', component: QqConnectionView, meta: { title: 'QQ连接' } },
    { path: '/chat-history', name: 'chat-history', component: ChatHistoryView, meta: { title: '聊天记录' } },
    { path: '/world-books', name: 'world-books', component: WorldBooksView, meta: { title: '世界书' } },
  ],
})
