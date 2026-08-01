import { expect, test } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  await page.route('**/api/auth/status', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ setup_required: false, authenticated: true, username: 'admin' }),
    })
  })
})

async function expectNoHorizontalOverflow(page: import('@playwright/test').Page) {
  const dimensions = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    content: document.documentElement.scrollWidth,
  }))
  expect(dimensions.content).toBeLessThanOrEqual(dimensions.viewport)
}

async function mockMemoryState(page: import('@playwright/test').Page) {
  const conversations = [
    {
      conversation_id: 'memory-visual-test',
      title: '钟楼之夜',
      external_id: 'test:memory',
      is_active: true,
      updated_at: '2026-07-25T12:00:00',
      message_count: 18,
    },
    {
      conversation_id: 'memory-empty-test',
      title: '尚未开场',
      external_id: 'test:empty-memory',
      is_active: false,
      updated_at: '2026-07-25T13:00:00',
      message_count: 0,
    },
  ]
  const memories = [
    { id: 'memory-file-a', name: '钟楼记忆', created_at: '2026-07-25T12:00:00', updated_at: '2026-07-25T12:00:00', bound_count: 1 },
    { id: 'memory-file-b', name: '空聊天记忆', created_at: '2026-07-25T13:00:00', updated_at: '2026-07-25T13:00:00', bound_count: 1 },
  ]
  const bindings: Record<string, string> = {
    'memory-visual-test': 'memory-file-a',
    'memory-empty-test': 'memory-file-b',
  }
  let createdMemoryCount = 0
  const state = {
    turn: 18,
    last_scene: { story_time: '第三日黄昏', location: '旧王城钟楼', summary: '众人在钟楼发现王家徽记。' },
    characters: [
      {
        id: 'char_lingnai', name: '玲奈', cast_role: 'lead', relationship_stage: '在意',
        affection: 24, trust: 31, jealousy: -4, user_relationship: '同行者', user_attitude: '开始信赖',
        physical_traits: '银白长发，紫色眼瞳', personality: '谨慎而温柔', occupation: '王族继承人',
        hobbies: ['阅读古籍'], likes: ['雨后的花园'], residence: '王城北塔',
        important_info: ['隐瞒王族身份'], current_outfit: '破损的白色斗篷', injuries: ['左肩箭伤'], last_turn: 18,
      },
      { id: 'char_xueyin', name: '雪音', cast_role: 'main_cast', relationship_stage: '相识', affection: 7, trust: 9, jealousy: 3, user_relationship: '调查对象', user_attitude: '保持戒备', personality: '冷静而执着', occupation: '王城调查官', last_turn: 17 },
      { id: 'char_you', name: '悠', cast_role: 'protagonist', relationship_stage: '信赖', affection: 18, trust: 20, jealousy: 0, user_relationship: '旅伴', user_attitude: '互相照应', personality: '沉稳', last_turn: 16 },
    ],
    relationships: [
      { id: 'relation_1', source: '玲奈', target: '雪音', relation: '旧识', attitude: '警惕', closeness: -12 },
      { id: 'relation_2', source: '雪音', target: '悠', relation: '合作对象', attitude: '观察中', closeness: 4 },
    ],
    events: [{ id: 'event_1', summary: '玲奈替悠挡下箭矢。', story_time: '第三日黄昏', location: '旧王城钟楼', arc: '王城徽记之谜', participants: ['玲奈', '悠'] }],
    promises: [{ id: 'promise_1', content: '悠答应暂时不追问玲奈的过去。', parties: ['悠', '玲奈'], status: 'pending' }],
    items: [{ id: 'item_1', name: '王家徽记', owner: '玲奈', status: '完好，已收起', location: '斗篷内袋' }],
  }

  const view = (selectedId: string) => {
    for (const memory of memories) {
      memory.bound_count = Object.values(bindings).filter((memoryId) => memoryId === memory.id).length
    }
    return {
      items: conversations.map((conversation) => {
        const memoryId = bindings[conversation.conversation_id]
        const memory = memories.find((item) => item.id === memoryId)
        return { ...conversation, memory_id: memoryId, memory_name: memory?.name ?? '未命名记忆' }
      }),
      memories,
      selected_id: selectedId,
      selected_memory_id: bindings[selectedId],
      state,
    }
  }

  await page.route('**/api/plugins/memory_system/conversation-states*', async (route) => {
    const selectedId = new URL(route.request().url()).searchParams.get('conversation_id')
      ?? conversations[0].conversation_id
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify(view(selectedId)),
    })
  })
  await page.route('**/api/plugins/memory_system/admin-actions/*', async (route) => {
    const action = new URL(route.request().url()).pathname.split('/').pop()
    const request = route.request().postDataJSON() as { payload?: Record<string, string> }
    const payload = request.payload ?? {}
    const conversationId = payload.conversation_id ?? conversations[0].conversation_id
    if (action === 'create-memory') {
      const id = `memory-created-${++createdMemoryCount}`
      memories.push({
        id,
        name: payload.name || '新记忆',
        created_at: '2026-07-25T14:00:00',
        updated_at: '2026-07-25T14:00:00',
        bound_count: 1,
      })
      bindings[conversationId] = id
    } else if (action === 'bind-memory' && payload.memory_id) {
      bindings[conversationId] = payload.memory_id
    } else if (action === 'rename-memory' && payload.memory_id) {
      const memory = memories.find((item) => item.id === payload.memory_id)
      if (memory) memory.name = payload.name || memory.name
    }
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ result: view(conversationId) }),
    })
  })
}

async function mockRegexState(page: import('@playwright/test').Page) {
  let state = { global_rules: [], character_rules: {} } as Record<string, unknown>
  await page.route('**/api/plugins/regex_filter/state', async (route) => {
    if (route.request().method() === 'PUT') {
      const payload = route.request().postDataJSON() as { state?: Record<string, unknown> }
      state = payload.state ?? state
    }
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ state }),
    })
  })
}

async function mockGroupChatState(page: import('@playwright/test').Page) {
  let state = {
    version: 2,
    global_words: [],
    groups: { '7788': { blocked_words: ['本群词'] } },
  } as Record<string, unknown>
  await page.route('**/api/plugins/group_chat_management/state', async (route) => {
    if (route.request().method() === 'PUT') {
      const payload = route.request().postDataJSON() as { state?: Record<string, unknown> }
      state = payload.state ?? state
    }
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ state }),
    })
  })
}

async function mockSearchModels(page: import('@playwright/test').Page) {
  await page.route('**/api/plugins/web_search/models', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify([
        { id: 'search-model-a', name: 'Search Model A' },
        { id: 'search-model-b', name: 'Search Model B' },
      ]),
    })
  })
}

function pngCharacterCard(data: Record<string, unknown>): Buffer {
  const metadata = Buffer.from(JSON.stringify(data), 'utf8').toString('base64')
  const text = Buffer.concat([Buffer.from('chara\0', 'latin1'), Buffer.from(metadata, 'latin1')])
  const chunk = (type: string, payload: Buffer) => {
    const length = Buffer.alloc(4)
    length.writeUInt32BE(payload.length)
    return Buffer.concat([length, Buffer.from(type, 'latin1'), payload, Buffer.alloc(4)])
  }
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk('tEXt', text),
    chunk('IEND', Buffer.alloc(0)),
  ])
}

test('first run creates an administrator account before opening the console', async ({ page }) => {
  await page.unroute('**/api/auth/status')
  await page.route('**/api/**', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: '[]' })
  })
  await page.route('**/api/auth/status', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ setup_required: true, authenticated: false, username: '' }),
    })
  })
  await page.route('**/api/auth/setup', async (route) => {
    const payload = route.request().postDataJSON() as { username: string }
    await route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify({ setup_required: false, authenticated: true, username: payload.username }),
    })
  })
  await page.route('**/api/overview', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        counts: { presets: 0, world_books: 0, providers: 0, templates: 0, characters: 0, user_personas: 0 },
        active_preset: null,
        active_provider: null,
        active_template: null,
        active_character: null,
        active_user_persona: null,
        active_world_book_ids: [],
      }),
    })
  })
  await page.route('**/api/logs*', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: '[]' })
  })

  await page.goto('/')
  await expect(page.getByRole('heading', { name: '创建管理员账号' })).toBeVisible()
  const authThemeSlider = page.getByLabel(/登录页主题色/)
  await expect(authThemeSlider).toHaveValue('1')
  await authThemeSlider.fill('4')
  await expect(authThemeSlider).toHaveValue('4')
  await expect.poll(() => page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--accent').trim())).toBe('#39c5bb')
  await expect(page.getByRole('button', { name: '创建并进入' })).toHaveCSS('background-color', 'rgb(57, 197, 187)')
  await page.screenshot({ path: 'test-results/auth-setup-desktop.png', fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  await expectNoHorizontalOverflow(page)
  await page.screenshot({ path: 'test-results/auth-setup-mobile.png', fullPage: true })
  await page.getByLabel('用户名').fill('管理员')
  await page.getByLabel('密码', { exact: true }).fill('correct-horse-battery')
  await page.getByLabel('确认密码').fill('different-password')
  await page.getByRole('button', { name: '创建并进入' }).click()
  await expect(page.getByText('两次输入的密码不一致')).toBeVisible()
  await page.getByLabel('确认密码').fill('correct-horse-battery')
  await page.getByRole('button', { name: '创建并进入' }).click()
  await expect(page.getByRole('heading', { name: '配置总览' })).toBeVisible()
  await expect(page.locator('.admin-session')).toHaveText('管理员')
})

test('live logs keep a manual scroll position while new entries arrive', async ({ page }) => {
  const initialLogs = Array.from({ length: 80 }, (_, index) => ({
    id: index + 1,
    created_at: `2026-07-30T12:${String(Math.floor(index / 60)).padStart(2, '0')}:${String(index % 60).padStart(2, '0')}Z`,
    level: 'INFO',
    source: 'catgirl.test',
    message: `历史日志 ${index + 1} ${'内容 '.repeat(12)}`,
  }))
  let incrementalRequests = 0
  await page.route('**/api/overview', async (route) => {
    await route.fulfill({
      json: {
        counts: { presets: 0, world_books: 0, providers: 0, templates: 0, characters: 0, user_personas: 0 },
        active_preset: null,
        active_provider: null,
        active_template: null,
        active_character: null,
        active_user_persona: null,
        active_world_book_ids: [],
      },
    })
  })
  await page.route('**/api/logs*', async (route) => {
    const afterId = Number(new URL(route.request().url()).searchParams.get('after_id') ?? 0)
    if (afterId === 0) {
      await route.fulfill({ json: initialLogs })
      return
    }
    incrementalRequests += 1
    await route.fulfill({
      json: incrementalRequests === 1
        ? [{ id: 81, created_at: '2026-07-30T12:02:00Z', level: 'INFO', source: 'catgirl.test', message: '最新日志' }]
        : [],
    })
  })

  await page.goto('/')
  const viewport = page.getByLabel('运行日志')
  await expect.poll(() => viewport.evaluate((element) => element.scrollTop)).toBeGreaterThan(0)
  await viewport.evaluate((element) => {
    element.scrollTop = 0
    element.dispatchEvent(new Event('scroll'))
  })
  await expect.poll(() => incrementalRequests).toBeGreaterThan(0)
  await expect(page.getByText('最新日志', { exact: true })).toBeAttached()
  await expect.poll(() => viewport.evaluate((element) => Math.round(element.scrollTop))).toBe(0)
})

test('desktop management pages render and remain usable', async ({ page, request }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.addInitScript(() => {
    if (!sessionStorage.getItem('theme-test-reset')) {
      localStorage.removeItem('catgirl.console.theme-stage.v1')
      sessionStorage.setItem('theme-test-reset', '1')
    }
  })
  await mockMemoryState(page)
  await mockRegexState(page)
  await mockGroupChatState(page)
  await mockSearchModels(page)
  await page.route('**/api/providers/*/models', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        { id: 'model-alpha', name: 'Model Alpha' },
        { id: 'model-beta', name: 'Model Beta' },
      ]),
    })
  })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '配置总览' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '日志' })).toBeVisible()
  await expect(page.getByLabel('运行日志')).toBeVisible()
  await expect(page.getByText('当前生效配置')).toBeVisible()
  const brandLogo = page.locator('.brand-mark img')
  await expect(brandLogo).toBeVisible()
  expect(await brandLogo.evaluate((image: HTMLImageElement) => image.complete && image.naturalWidth > 0)).toBe(true)
  await expect(page.locator('link[rel="icon"]')).toHaveAttribute('href', '/catgirl-favicon-transparent.png')
  const themeSlider = page.getByLabel(/主题色/)
  await expect(themeSlider).toHaveValue('1')
  await themeSlider.evaluate((element: HTMLInputElement) => {
    element.value = '5'
    element.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await expect.poll(() => page.evaluate(() => localStorage.getItem('catgirl.console.theme-stage.v1'))).toBe('5')
  await page.reload()
  await expect(page.getByLabel(/主题色：蓝色/)).toHaveValue('5')
  await page.getByLabel(/主题色：蓝色/).evaluate((element: HTMLInputElement) => {
    element.value = '1'
    element.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await expectNoHorizontalOverflow(page)
  await page.screenshot({ path: 'test-results/dashboard-desktop.png', fullPage: true })

  await page.getByRole('link', { name: '预设配置', exact: true }).click()
  await expect(page.getByRole('heading', { name: '预设配置', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: '组合资源' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '上下文与回复' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '采样参数' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '消息处理' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '模型能力' })).toBeVisible()
  await expect(page.getByLabel('API 供应商')).toBeVisible()
  await expect(page.getByRole('button', { name: '导入配置' })).toBeVisible()
  await expect(page.getByRole('button', { name: '导入配置' }).locator('svg.lucide-download')).toBeVisible()
  await expect(page.getByTitle('导出整套预设')).toBeVisible()
  await expect(page.getByTitle('导出整套预设').locator('svg.lucide-upload')).toBeVisible()
  await expect(page.getByText('世界书（可多选）')).toBeVisible()
  await expect(page.getByLabel('用户人设')).toBeVisible()
  await expect(page.getByText('解锁上下文长度')).toBeVisible()
  await expectNoHorizontalOverflow(page)
  await page.screenshot({ path: 'test-results/presets-desktop.png', fullPage: true })

  await page.getByRole('link', { name: 'API配置', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'API配置', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'API 接口配置', exact: true })).toBeVisible()
  await expect(page.getByRole('radio', { name: 'OpenAI 兼容' })).toBeVisible()
  await expect(page.getByRole('radio', { name: 'Anthropic' })).toBeVisible()
  await expect(page.getByRole('radio', { name: 'Gemini / Google' })).toBeVisible()
  await expect(page.getByLabel('聊天补全来源')).toBeVisible()
  await expect(page.getByLabel('聊天补全来源').locator('option')).toHaveCount(25)
  await expect(page.getByLabel('提示词后处理')).toBeVisible()
  await expect(page.getByLabel('故障转移序号')).toBeVisible()
  await expect(page.getByTitle('导出 API 配置')).toBeVisible()
  await expect(page.getByTitle('导出 API 配置').locator('svg.lucide-upload')).toBeVisible()
  await page.getByLabel('聊天补全来源').selectOption('claude')
  await expect(page.getByRole('radio', { name: 'Anthropic' })).toHaveAttribute('aria-checked', 'true')
  await expect(page.getByLabel('Base URL')).toHaveValue('https://api.anthropic.com/v1')
  await page.getByLabel('聊天补全来源').selectOption('custom')
  await expect(page.getByTitle('拉取模型列表')).toBeVisible()
  await page.getByLabel('Base URL').fill('https://models.test/v1')
  await page.getByTitle('拉取模型列表').click()
  await expect(page.getByLabel('模型名称').locator('option')).toHaveCount(3)
  await expect(page.getByLabel('Base URL')).toBeVisible()
  await expect(page.getByRole('button', { name: '测试连接' })).toBeVisible()
  await expectNoHorizontalOverflow(page)
  await page.screenshot({ path: 'test-results/providers-desktop.png', fullPage: true })

  const previewRecordResponse = await request.post('/api/runtime/conversations', {
    data: { route_id: `preview:${Date.now()}`, title: '提示词预览记录' },
  })
  expect(previewRecordResponse.ok()).toBeTruthy()
  const previewRecord = await previewRecordResponse.json() as { id: string }
  await page.getByRole('link', { name: '提示词编辑', exact: true }).click()
  await expect(page.getByRole('heading', { name: '提示词编辑', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: '提示词块' }).first()).toBeVisible()
  await expect(page.getByText('实际发送预览')).toBeVisible()
  await expect(page.locator('.preview-total-tokens')).toContainText('总 ')
  await expect(page.locator('.preview-token-count').first()).toContainText('tokens')
  await expect(page.locator('.preview-message.kind-history')).toBeVisible()
  await expect(page.locator('.preview-message.kind-history pre')).toHaveCount(0)
  await expect(page.locator('.preview-message.kind-history')).not.toContainText('marker')
  await expect(page.locator('.preview-message.kind-history .preview-token-count')).toHaveText('0 tokens')
  await expect(page.locator('.preview-message.kind-plugin')).toHaveCount(2)
  await expect(page.locator('.preview-message.kind-plugin').first().locator('.preview-injection')).toBeVisible()
  await expect(page.getByTitle('导出提示词模板')).toBeVisible()
  await expect(page.getByTitle('导出提示词模板').locator('svg.lucide-upload')).toBeVisible()
  await expect(page.locator('.prompt-block-row')).toHaveCount(2)
  await page.locator('.prompt-block-row').first().click()
  await expect(page.locator('.grow-field textarea')).toHaveCSS('resize', 'none')
  await expect(page.locator('.grow-field textarea')).toHaveCSS('font-size', '12px')
  await expect(page.getByText('插入方式', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '聊天中指定深度' })).toBeVisible()
  await expect(page.getByText('动态标识符', { exact: true })).toBeVisible()
  await expect(page.getByLabel('插入宏')).toBeVisible()
  expect(await page.getByLabel('插入宏').locator('option').count()).toBeGreaterThan(70)
  await expect(page.locator('.preview-message').first()).toHaveCSS('flex-shrink', '0')
  await expectNoHorizontalOverflow(page)
  await page.screenshot({ path: 'test-results/prompts-desktop.png', fullPage: true })
  await request.delete(`/api/runtime/conversations/${previewRecord.id}`)

  await page.getByRole('link', { name: '用户人设', exact: true }).click()
  await expect(page.getByRole('heading', { name: '用户人设', exact: true })).toBeVisible()
  await expect(page.getByLabel('用户名称')).toBeVisible()
  await expect(page.getByLabel('用户人设描述')).toBeVisible()
  await expect(page.getByTitle('导出用户人设')).toBeVisible()
  await expect(page.getByTitle('导出用户人设').locator('svg.lucide-upload')).toBeVisible()
  await expect(page.getByLabel('描述插入位置')).toBeVisible()
  await expectNoHorizontalOverflow(page)
  await page.screenshot({ path: 'test-results/user-personas-desktop.png', fullPage: true })

  await page.getByRole('link', { name: '角色人设', exact: true }).click()
  await expect(page.getByRole('heading', { name: '角色人设', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '导入角色卡' })).toBeVisible()
  await expect(page.getByRole('button', { name: '导入角色卡' }).locator('svg.lucide-download')).toBeVisible()
  await expect(page.getByLabel('角色设定')).toBeVisible()
  await expect(page.getByText('链接到世界书')).toBeVisible()
  await expect(page.getByTitle('导出角色卡')).toBeVisible()
  await expect(page.getByTitle('导出角色卡').locator('svg.lucide-upload')).toBeVisible()
  await expect(page.getByRole('button', { name: '保存' })).toBeVisible()

  await page.getByRole('link', { name: '世界书', exact: true }).click()
  await expect(page.getByRole('button', { name: '导入世界书' })).toBeVisible()
  await expect(page.getByRole('button', { name: '导入世界书' }).locator('svg.lucide-file-down')).toBeVisible()
  await expectNoHorizontalOverflow(page)

  await page.getByRole('link', { name: '插件', exact: true }).click()
  await expect(page.getByText('Python 插件拥有服务器进程权限')).toBeVisible()
  expect(await page.locator('.plugin-drag-handle').count()).toBeGreaterThanOrEqual(9)
  await expect(page.locator('.plugin-drag-handle').first()).toHaveAttribute('title', '拖动排序')
  await expect(page.getByRole('button', { name: /网络搜索/ })).toBeVisible()
  await page.getByRole('button', { name: /群聊管理/ }).click()
  await expect(page.getByRole('textbox', { name: /^AI 唤醒词/ })).toHaveValue('')
  await expect(page.getByRole('checkbox', { name: /^@AI 后回复/ })).not.toBeChecked()
  const replacementSymbol = page.getByRole('textbox', { name: /^屏蔽词替换符号/ })
  await expect(replacementSymbol).toHaveValue('*')
  await expect(replacementSymbol).toHaveAttribute('maxlength', '1')
  await expect(page.getByRole('textbox', { name: /^添加屏蔽词命令/ })).toHaveValue('/添加屏蔽词 xxx')
  await expect(page.getByRole('textbox', { name: /^移除屏蔽词命令/ })).toHaveValue('/移除屏蔽词 xxx')
  await expect(page.getByRole('textbox', { name: /^屏蔽词列表命令/ })).toHaveValue('/屏蔽词列表')
  await expect(page.getByRole('textbox', { name: /^清空屏蔽词命令/ })).toHaveValue('/清空屏蔽词')
  const groupChatEditor = page.locator('.group-chat-editor-section')
  await expect(groupChatEditor.getByRole('heading', { name: '屏蔽词' })).toBeVisible()
  await expect(groupChatEditor.getByRole('tab', { name: '全局屏蔽词' })).toHaveAttribute('aria-selected', 'true')
  await groupChatEditor.getByLabel('屏蔽词', { exact: true }).fill('全局测试词')
  await groupChatEditor.getByRole('button', { name: '添加', exact: true }).click()
  await expect(groupChatEditor.getByText('全局测试词', { exact: true })).toBeVisible()
  await groupChatEditor.getByRole('button', { name: '保存', exact: true }).click()
  await expect(page.getByText('屏蔽词已保存')).toBeVisible()
  await groupChatEditor.getByRole('tab', { name: '分群屏蔽词' }).click()
  await expect(groupChatEditor.locator('.group-chat-group-select select')).toHaveValue('7788')
  await expect(groupChatEditor.getByText('本群词', { exact: true })).toBeVisible()
  await expectNoHorizontalOverflow(page)
  await page.screenshot({ path: 'test-results/group-chat-management-desktop.png', fullPage: true })
  await page.getByRole('button', { name: /^正则/ }).click()
  await expect(page.getByRole('heading', { name: '正则脚本' })).toBeVisible()
  await expect(page.getByRole('tab', { name: '全局正则' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByText('尚未添加全局正则')).toBeVisible()
  await page.getByRole('button', { name: '添加正则' }).click()
  await expect(page.locator('.regex-rule-item')).toHaveCount(1)
  await page.getByLabel('正则名称').fill('隐藏测试块')
  await page.locator('.regex-pattern-field textarea').fill('<debug>[\\s\\S]*?</debug>')
  await page.locator('.regex-rule-toggle').click()
  await page.getByRole('button', { name: '保存', exact: true }).click()
  await expect(page.getByText('正则脚本已保存')).toBeVisible()
  await page.screenshot({ path: 'test-results/regex-editor-desktop.png', fullPage: true })
  await page.getByRole('tab', { name: '角色正则' }).click()
  await expect(page.locator('.regex-character-select select')).toBeVisible()
  await expect(page.getByRole('button', { name: '停用', exact: true })).toHaveCount(0)
  await expectNoHorizontalOverflow(page)
  await page.getByRole('button', { name: /记忆系统/ }).click()
  await expect(page.getByRole('heading', { name: '记忆可视化' })).toBeVisible()
  await expect(page.getByTitle('导出当前聊天记忆')).toBeVisible()
  await expect(page.getByLabel('聊天记录').locator('option')).toContainText(['钟楼之夜 · 当前', '尚未开场 · 空聊天'])
  await page.getByLabel('聊天记录').selectOption('memory-empty-test')
  await expect(page.getByLabel('绑定记忆')).toHaveValue('memory-file-b')
  await page.getByLabel('绑定记忆').selectOption('memory-file-a')
  await expect(page.getByLabel('绑定记忆')).toHaveValue('memory-file-a')
  await page.getByLabel('记忆名称').fill('空聊天独立记忆')
  await page.getByTitle('新建并绑定独立记忆').click()
  await expect(page.getByLabel('绑定记忆')).toHaveValue('memory-created-1')
  await page.getByLabel('记忆名称').fill('手动重命名记忆')
  await page.getByTitle('保存记忆名称').click()
  await expect(page.getByLabel('绑定记忆').locator('option:checked')).toHaveText('手动重命名记忆')
  await expect(page.getByLabel('人物关系网络')).toBeVisible()
  await page.getByRole('button', { name: /玲奈/ }).click()
  await expect(page.getByRole('heading', { name: '玲奈' })).toBeVisible()
  await page.getByRole('tab', { name: /社交关系/ }).click()
  await expect(page.getByText(/旧识/)).toBeVisible()
  await page.screenshot({ path: 'test-results/memory-network-desktop.png', fullPage: true })
  await expect(page.getByText('核心角色名称（可选）')).toBeVisible()
  const compressionSetting = page.locator('label').filter({ hasText: '分层压缩触发事件数' })
  await expect(compressionSetting).toHaveAttribute('title', /独立事件超过此数量/)
  await expect(compressionSetting).toHaveAttribute('title', /允许范围：40 - 1000/)
  await expect(compressionSetting.getByText('允许范围：40 - 1000')).toBeVisible()
  await page.getByRole('button', { name: /主动回复/ }).click()
  await expect(page.getByText('最短等待（分钟）')).toBeVisible()
  await expect(page.getByRole('button', { name: '保存设置' })).toBeVisible()
  await page.getByRole('button', { name: /回复合并/ }).click()
  await expect(page.getByText('合并等待时间（秒）')).toBeVisible()
  await page.getByRole('button', { name: /表情回复/ }).click()
  await expect(page.getByRole('button', { name: '打开表情文件夹' })).toBeVisible()
  await page.screenshot({ path: 'test-results/sticker-folder-button-desktop.png', fullPage: true })
  await page.getByRole('button', { name: /时间感知/ }).click()
  await expect(page.locator('label').filter({ hasText: '时间感知提示词' }).locator('textarea')).toBeVisible()
  await page.getByRole('button', { name: /睡眠模拟/ }).click()
  await expect(page.getByText('双方互道休眠关键词后（晚安/午安）后AI进入休眠，会暂存消息并在醒来时统一生成问候与回复。')).toBeVisible()
  await expect(page.locator('label').filter({ hasText: '休眠关键词' }).locator('input')).toBeVisible()
  await page.getByRole('button', { name: /网络搜索/ }).click()
  await expect(page.locator('label').filter({ hasText: '网络搜索提示词' }).locator('textarea')).toBeVisible()
  const searchModelPrompt = page.locator('label').filter({ hasText: '搜索模型内置提示词' }).locator('textarea')
  await expect(searchModelPrompt).toBeVisible()
  await expect(searchModelPrompt).toHaveValue(/^<RealTime_Search>\n/)
  await expect(searchModelPrompt).not.toHaveValue(/交叉核验/)
  await expect(page.getByText('填写接入的搜索模型名称。（如 gemini-3.1-pro-preview-search 等自带搜索工具的模型）')).toBeVisible()
  const searchEngine = page.locator('label').filter({ hasText: '搜索方式' }).locator('select')
  await expect(searchEngine).toBeVisible()
  await expect(searchEngine.locator('option')).toHaveCount(6)
  await expect(searchEngine.locator('option')).toHaveText([
    '自定义搜索模型',
    'Sear (SearXNG)',
    'DuckDuckGo',
    'Google',
    'Serp (SerpApi)',
    'Bing',
  ])
  await searchEngine.selectOption('model')
  await page.locator('label').filter({ hasText: '搜索模型 API 地址' }).locator('input').fill('https://search-model.test/v1')
  await page.getByTitle('拉取搜索模型列表').click()
  const searchModelSelect = page.getByLabel('搜索模型名称')
  await expect(searchModelSelect).toHaveValue('')
  await expect(searchModelSelect.locator('option')).toHaveCount(3)
  await searchModelSelect.selectOption('search-model-b')
  await expect(searchModelSelect).toHaveValue('search-model-b')
  await expect(page.locator('label').filter({ hasText: '搜索模型 API Key' }).locator('input')).toHaveAttribute('type', 'password')
  await expect(page.locator('label').filter({ hasText: 'SerpApi Key' }).locator('input')).toHaveAttribute('type', 'password')
  await expectNoHorizontalOverflow(page)
  await page.screenshot({ path: 'test-results/plugins-desktop.png', fullPage: true })

  await page.getByRole('link', { name: 'QQ连接', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'QQ连接', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: '连接状态' })).toBeVisible()
  await expect(page.getByText('反向 WebSocket 监听地址', { exact: true })).toBeVisible()
  const reverseAddress = page.getByLabel('反向 WebSocket 监听地址')
  await expect(reverseAddress).toBeEditable()
  await reverseAddress.fill('wss://bot.example.test/onebot/v11/ws')
  await expect(reverseAddress).toHaveValue('wss://bot.example.test/onebot/v11/ws')
  await page.getByRole('button', { name: '正向 WS', exact: true }).click()
  await expect(page.getByText('NapCat 正向 WebSocket 地址', { exact: true })).toBeVisible()
  await expect(page.getByPlaceholder('ws://127.0.0.1:3001')).toBeVisible()
  await page.getByRole('button', { name: '反向 WS', exact: true }).click()
  await expect(page.getByLabel('反向 WebSocket 监听地址')).toHaveValue('wss://bot.example.test/onebot/v11/ws')
  await expect(page.getByText('处理私聊消息')).toBeVisible()
  await expect(page.getByRole('button', { name: '保存配置' })).toBeVisible()
  await expectNoHorizontalOverflow(page)
  await page.screenshot({ path: 'test-results/qq-connection-desktop.png', fullPage: true })
})

test('PNG character cards are decoded and sent through the character importer', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  let importPayload: Record<string, unknown> | null = null
  await page.route('**/api/import/sillytavern', async (route) => {
    importPayload = route.request().postDataJSON() as Record<string, unknown>
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        preset_id: null,
        preset_name: null,
        prompt_template_id: null,
        provider_id: null,
        world_book_ids: ['embedded-book'],
        character_ids: ['imported-card'],
        imported_characters: 1,
        imported_prompt_blocks: 0,
        imported_world_entries: 1,
        warnings: ['角色卡“玲奈”的 PNG 立绘未保存，仅导入卡片文本数据'],
      }),
    })
  })
  await page.goto('/#/characters')
  await expect(page.getByRole('button', { name: '导入角色卡' })).toBeVisible()
  await page.locator('input[type="file"]').setInputFiles({
    name: 'lingnai.png',
    mimeType: 'image/png',
    buffer: pngCharacterCard({
      spec: 'chara_card_v2',
      spec_version: '2.0',
      data: {
        name: '玲奈',
        description: '银发的王族继承人。',
        first_mes: '你终于来了。',
        character_book: { entries: [{ id: 1, keys: ['王城'], content: '北方王城。' }] },
      },
    }),
  })

  await expect(page.getByText(/已导入：1 张角色卡、1 本内嵌世界书/)).toBeVisible()
  expect(importPayload).not.toBeNull()
  const characters = importPayload?.characters as Array<{ data: Record<string, unknown> }>
  expect(characters).toHaveLength(1)
  expect(characters[0].data.__catgirl_source_format).toBe('png')
  expect((characters[0].data.data as Record<string, unknown>).name).toBe('玲奈')
  expect(importPayload?.world_books).toEqual([])
  await expectNoHorizontalOverflow(page)
})

test('mobile prompt workbench has no horizontal overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/#/prompts')
  await expect(page.getByRole('heading', { name: '提示词编辑' })).toBeVisible()
  await expect(page.getByText('实际发送预览')).toBeVisible()
  await expect(page.locator('.prompt-template-rail')).toBeVisible()
  await expectNoHorizontalOverflow(page)
  await page.screenshot({ path: 'test-results/prompts-mobile.png', fullPage: true })
})

test('mobile preset editor exposes the complete preset without overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/#/presets')
  await expect(page.getByRole('heading', { name: '预设配置' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '组合资源' })).toBeVisible()
  await expect(page.getByText('请求思维链')).toBeVisible()
  await expectNoHorizontalOverflow(page)
  await page.screenshot({ path: 'test-results/presets-mobile.png', fullPage: true })
})

test('mobile world-book page and ten-item navigation do not overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/#/world-books')
  await expect(page.getByRole('heading', { name: '世界书' })).toBeVisible()
  await expect(page.getByRole('button', { name: '导入世界书' })).toBeVisible()
  await expect(page.locator('.main-nav a')).toHaveCount(10)
  await expectNoHorizontalOverflow(page)
  await page.screenshot({ path: 'test-results/world-books-mobile.png', fullPage: true })
})

test('world-book scope, ordering and folder export work together', async ({ page, request }) => {
  const name = `世界书回归 ${Date.now()}`
  const bookResponse = await request.post('/api/world-books', {
    data: { name, description: '边框间距检查' },
  })
  expect(bookResponse.ok()).toBeTruthy()
  const book = await bookResponse.json() as { id: string }
  const lowResponse = await request.post(`/api/world-books/${book.id}/entries`, {
    data: { comment: '低顺序', insertion_order: 10 },
  })
  const highResponse = await request.post(`/api/world-books/${book.id}/entries`, {
    data: { comment: '高顺序', insertion_order: 20 },
  })
  const low = await lowResponse.json() as { id: string }
  expect(highResponse.ok()).toBeTruthy()

  await page.addInitScript(() => {
    ;(window as any).__catgirlExport = ''
    ;(window as any).showDirectoryPicker = async () => ({
      getFileHandle: async () => ({
        createWritable: async () => ({
          write: async (value: string) => { (window as any).__catgirlExport = value },
          close: async () => undefined,
        }),
      }),
    })
  })

  try {
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/#/world-books')
    await page.getByRole('button', { name: new RegExp(name) }).click()
    await expect(page.getByLabel('作用范围')).toHaveValue('character')
    await expect(page.getByLabel('关联角色')).not.toHaveValue('')
    await expect(page.getByLabel('世界书名称')).toHaveCSS('padding-left', '8px')
    await expect(page.locator('.world-entry-row strong').first()).toHaveText('低顺序')

    await page.getByText('低顺序', { exact: true }).click()
    await page.getByLabel('插入顺序').fill('30')
    await page.getByRole('button', { name: '保存条目' }).click()
    await expect(page.locator('.world-entry-row strong').first()).toHaveText('高顺序')

    await page.getByTitle('导出世界书').click()
    await expect.poll(() => page.evaluate(() => (window as any).__catgirlExport)).toContain(name)
    await expectNoHorizontalOverflow(page)
    await page.screenshot({ path: 'test-results/world-book-scope-desktop.png', fullPage: true })
  } finally {
    await request.delete(`/api/world-books/${book.id}`)
    await request.delete(`/api/world-book-entries/${low.id}`).catch(() => undefined)
  }
})

test('mobile plugin manager keeps settings and navigation usable', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await mockMemoryState(page)
  await mockRegexState(page)
  await mockGroupChatState(page)
  await mockSearchModels(page)
  await page.goto('/#/plugins')
  await expect(page.getByRole('heading', { name: '插件', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: /网络搜索/ })).toBeVisible()
  await page.getByRole('button', { name: /群聊管理/ }).click()
  await expect(page.getByRole('textbox', { name: /^AI 唤醒词/ })).toBeVisible()
  await expect(page.getByRole('checkbox', { name: /^@AI 后回复/ })).toBeVisible()
  await expect(page.getByRole('textbox', { name: /^屏蔽词替换符号/ })).toBeVisible()
  await expect(page.getByRole('textbox', { name: /^添加屏蔽词命令/ })).toBeVisible()
  await expect(page.getByRole('textbox', { name: /^移除屏蔽词命令/ })).toBeVisible()
  await expect(page.getByRole('textbox', { name: /^屏蔽词列表命令/ })).toBeVisible()
  await expect(page.getByRole('textbox', { name: /^清空屏蔽词命令/ })).toBeVisible()
  const mobileGroupChatEditor = page.locator('.group-chat-editor-section')
  await expect(mobileGroupChatEditor.getByRole('tab', { name: '全局屏蔽词' })).toBeVisible()
  await mobileGroupChatEditor.getByRole('tab', { name: '分群屏蔽词' }).click()
  await expect(mobileGroupChatEditor.locator('.group-chat-group-select select')).toHaveValue('7788')
  await expectNoHorizontalOverflow(page)
  await page.screenshot({ path: 'test-results/group-chat-management-mobile.png', fullPage: true })
  await page.getByRole('button', { name: /回复合并/ }).click()
  await expect(page.getByText('合并等待时间（秒）')).toBeVisible()
  await page.getByRole('button', { name: /表情回复/ }).click()
  await expect(page.getByRole('button', { name: '打开表情文件夹' })).toBeVisible()
  await page.screenshot({ path: 'test-results/sticker-folder-button-mobile.png', fullPage: true })
  await page.getByRole('button', { name: /时间感知/ }).click()
  await expect(page.locator('label').filter({ hasText: '时间感知提示词' }).locator('textarea')).toBeVisible()
  await page.getByRole('button', { name: /睡眠模拟/ }).click()
  await expect(page.locator('label').filter({ hasText: '休眠关键词' }).locator('input')).toBeVisible()
  await page.getByRole('button', { name: /网络搜索/ }).click()
  const searchPrompt = page.locator('label').filter({ hasText: '网络搜索提示词' }).locator('textarea')
  await expect(searchPrompt).toBeVisible()
  await expect(page.locator('label').filter({ hasText: '搜索模型内置提示词' }).locator('textarea')).toBeVisible()
  await searchPrompt.evaluate(element => element.scrollIntoView({ block: 'center' }))
  const searchPromptBox = await searchPrompt.boundingBox()
  const promptNavBox = await page.locator('.sidebar').boundingBox()
  expect(searchPromptBox).not.toBeNull()
  expect(promptNavBox).not.toBeNull()
  expect((searchPromptBox?.y ?? 0) + (searchPromptBox?.height ?? 0)).toBeLessThanOrEqual(promptNavBox?.y ?? 0)
  await expectNoHorizontalOverflow(page)
  await page.screenshot({ path: 'test-results/plugin-prompts-mobile.png' })
  await page.getByRole('button', { name: /^正则/ }).click()
  await page.getByRole('button', { name: '添加正则' }).click()
  await expect(page.locator('.regex-rule-item')).toHaveCount(1)
  const regexFlags = page.locator('.regex-flags')
  await regexFlags.scrollIntoViewIfNeeded()
  const flagsBox = await regexFlags.boundingBox()
  const mobileNavBox = await page.locator('.sidebar').boundingBox()
  expect(flagsBox).not.toBeNull()
  expect(mobileNavBox).not.toBeNull()
  expect((flagsBox?.y ?? 0) + (flagsBox?.height ?? 0)).toBeLessThanOrEqual(mobileNavBox?.y ?? 0)
  await page.screenshot({ path: 'test-results/regex-editor-mobile.png' })
  await page.getByRole('tab', { name: '角色正则' }).click()
  await expect(page.locator('.regex-character-select select')).toBeVisible()
  await expectNoHorizontalOverflow(page)
  await page.getByRole('button', { name: /记忆系统/ }).click()
  await expect(page.getByRole('heading', { name: '记忆可视化' })).toBeVisible()
  await expect(page.getByLabel('人物关系网络')).toBeVisible()
  await page.getByRole('button', { name: /玲奈/ }).click()
  await expect(page.getByText('身体特征', { exact: true })).toBeVisible()
  await expect(page.locator('.main-nav a')).toHaveCount(10)
  await expectNoHorizontalOverflow(page)
  await page.screenshot({ path: 'test-results/memory-network-mobile.png', fullPage: true })
})

test('mobile QQ connection settings use a two-row navigation without overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/#/qq-connection')
  await expect(page.getByRole('heading', { name: 'QQ连接', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: '连接状态' })).toBeVisible()
  await expect(page.getByText('反向 WebSocket 监听地址', { exact: true })).toBeVisible()
  await expect(page.locator('.main-nav a')).toHaveCount(10)
  await expect(page.locator('.sidebar')).toHaveCSS('height', '112px')
  await expectNoHorizontalOverflow(page)
  await page.screenshot({ path: 'test-results/qq-connection-mobile.png', fullPage: true })
})

test('chat records for one QQ can be previewed and switched on mobile', async ({ page, request }) => {
  const target = String(Date.now())
  const route = `qq:90001:private:${target}`
  const firstResponse = await request.post('/api/runtime/conversations', {
    data: { route_id: route, title: '记录甲' },
  })
  const secondResponse = await request.post('/api/runtime/conversations', {
    data: { route_id: route, title: '记录乙' },
  })
  expect(firstResponse.ok()).toBeTruthy()
  expect(secondResponse.ok()).toBeTruthy()
  const first = await firstResponse.json() as { id: string }
  const second = await secondResponse.json() as { id: string }

  try {
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto('/#/chat-history')
    await expect(page.getByRole('heading', { name: '聊天记录', exact: true })).toBeVisible()
    await page.locator('.history-route-select select').selectOption(route)
    await expect(page.locator('.history-route-select select')).toHaveValue(route)
    await expect(page.locator(`.history-route-select option[value="${route}"]`)).toHaveText('私聊 · 未选择角色')
    await expect(page.locator('.history-record-row')).toHaveCount(2)
    await page.getByRole('button', { name: /记录乙/ }).click()
    await expect(page.getByRole('button', { name: '使用这份记录' })).toBeVisible()
    await page.getByRole('button', { name: '使用这份记录' }).click()
    await expect(page.getByText('当前使用', { exact: true })).toBeVisible()
    await expect(page.getByText('这份记录还没有消息')).toBeVisible()
    await page.getByLabel('聊天记录名称').fill('记录乙已切换')
    await page.getByRole('button', { name: '保存名称' }).click()
    await expect(page.getByLabel('聊天记录名称')).toHaveValue('记录乙已切换')
    await expect(page.getByTitle('导出聊天记录')).toBeVisible()
    await expect(page.getByRole('button', { name: '导入', exact: true })).toBeVisible()
    await expect(page.locator('.main-nav a')).toHaveCount(10)
    await expect(page.locator('.sidebar')).toHaveCSS('height', '112px')
    await expectNoHorizontalOverflow(page)
    await page.screenshot({ path: 'test-results/chat-history-mobile.png', fullPage: true })
  } finally {
    await request.delete(`/api/runtime/conversations/${first.id}`)
    await request.delete(`/api/runtime/conversations/${second.id}`)
  }
})

test('SillyTavern chat files can be imported into the selected QQ record', async ({ page, request }) => {
  const target = String(Date.now())
  const route = `qq:90001:private:${target}`
  const originalResponse = await request.post('/api/runtime/conversations', {
    data: { route_id: route, title: '导入目标' },
  })
  expect(originalResponse.ok()).toBeTruthy()
  const original = await originalResponse.json() as { id: string }

  try {
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/#/chat-history')
    await page.locator('.history-route-select select').selectOption(route)
    const content = [
      { user_name: '墨墨', character_name: '柏柏', create_date: '2026-07-01T12:00:00Z', chat_metadata: {} },
      { name: '墨墨', is_user: true, is_system: false, send_date: '2026-07-01T12:01:00Z', mes: '从酒馆导入的提问' },
      { name: '柏柏', is_user: false, is_system: false, send_date: '2026-07-01T12:02:00Z', mes: '从酒馆导入的回复', extra: { reasoning: '导入的思考内容' } },
    ].map((item) => JSON.stringify(item)).join('\n')
    await page.locator('input[type="file"][accept*=".jsonl"]').setInputFiles({
      name: '酒馆旧记录.jsonl',
      mimeType: 'application/x-ndjson',
      buffer: Buffer.from(content, 'utf-8'),
    })

    await expect(page.getByText('已导入 1 份记录，共 2 条消息')).toBeVisible()
    await expect(page.getByLabel('聊天记录名称')).toHaveValue('酒馆旧记录')
    await expect(page.getByRole('button', { name: '使用这份记录' })).toBeVisible()
    await expect(page.getByText('从酒馆导入的提问')).toBeVisible()
    await expect(page.getByText('从酒馆导入的回复')).toBeVisible()
    await expect(page.getByText('墨墨', { exact: true })).toBeVisible()
    await expect(page.getByText('柏柏', { exact: true })).toBeVisible()
    const reasoning = page.locator('.history-reasoning')
    await reasoning.locator('summary').click()
    await expect(reasoning.locator('pre')).toHaveText('导入的思考内容')

    const recordsResponse = await request.get('/api/runtime/conversations')
    const records = await recordsResponse.json() as Array<{ id: string, external_id: string, is_active: boolean }>
    const routeRecords = records.filter((item) => item.external_id === route)
    expect(routeRecords).toHaveLength(2)
    expect(routeRecords.find((item) => item.id === original.id)?.is_active).toBe(true)
    expect(routeRecords.find((item) => item.id !== original.id)?.is_active).toBe(false)
  } finally {
    const recordsResponse = await request.get('/api/runtime/conversations')
    const records = await recordsResponse.json() as Array<{ id: string, external_id: string }>
    for (const record of records.filter((item) => item.external_id === route && item.id !== original.id)) {
      await request.delete(`/api/runtime/conversations/${record.id}`)
    }
    await request.delete(`/api/runtime/conversations/${original.id}`)
  }
})

test('chat history multi-select deletes only the selected messages', async ({ page }) => {
  const route = 'qq:90001:private:123456'
  const record = {
    id: 'record-multi-select',
    channel: 'private',
    external_id: route,
    title: '可编辑记录',
    is_active: true,
    message_count: 3,
    total_tokens: 1234,
    character_name: '欢欢',
    last_message_preview: '第三条',
    created_at: '2026-07-29T05:00:00Z',
    updated_at: '2026-07-29T05:03:00Z',
  }
  let messages = [
    { id: 'message-1', conversation_id: record.id, position: 0, role: 'user', content: '第一条', status: 'complete', source: 'user', provider_id: null, preset_id: null, model: '', prompt_tokens: null, completion_tokens: null, total_tokens: null, token_count: 400, speaker_name: '墨墨', message_metadata: {}, created_at: '2026-07-29T05:00:00Z' },
    { id: 'message-2', conversation_id: record.id, position: 1, role: 'assistant', content: '第二条', status: 'complete', source: 'runtime', provider_id: null, preset_id: null, model: 'test-model', prompt_tokens: 8, completion_tokens: 3, total_tokens: 11, token_count: 400, speaker_name: '欢欢', message_metadata: { reasoning: '先分析用户的问题，再组织回答。' }, created_at: '2026-07-29T05:01:00Z' },
    { id: 'message-3', conversation_id: record.id, position: 2, role: 'user', content: '第三条', status: 'complete', source: 'user', provider_id: null, preset_id: null, model: '', prompt_tokens: null, completion_tokens: null, total_tokens: null, token_count: 434, speaker_name: '墨墨', message_metadata: {}, created_at: '2026-07-29T05:02:00Z' },
  ]
  let deletedIds: string[] = []

  await page.route('**/api/runtime/conversations', async (routeHandler) => {
    const currentRecord = {
      ...record,
      message_count: messages.length,
      total_tokens: messages.reduce((sum, message) => sum + message.token_count, 0),
    }
    await routeHandler.fulfill({ json: [currentRecord] })
  })
  await page.route(`**/api/runtime/conversations/${record.id}/messages`, async (routeHandler) => {
    await routeHandler.fulfill({ json: messages })
  })
  await page.route(`**/api/runtime/conversations/${record.id}/messages/delete`, async (routeHandler) => {
    const body = routeHandler.request().postDataJSON() as { message_ids: string[] }
    deletedIds = body.message_ids
    messages = messages.filter((message) => !deletedIds.includes(message.id))
    await routeHandler.fulfill({ json: { deleted_count: deletedIds.length, remaining_count: messages.length } })
  })

  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/#/chat-history')
  await expect(page.locator('.history-route-select option')).toHaveText('私聊 · 欢欢')
  await expect(page.getByText('3 条消息 · 总 1,234 tokens')).toBeVisible()
  const reasoning = page.locator('.history-reasoning')
  await expect(reasoning).toHaveCount(1)
  await expect(reasoning.locator('pre')).not.toBeVisible()
  await reasoning.locator('summary').click()
  await expect(reasoning.locator('pre')).toHaveText('先分析用户的问题，再组织回答。')
  await expect(reasoning.locator('pre')).toBeVisible()
  await page.getByRole('button', { name: '多选' }).click()
  await expect(page.locator('.history-message-checkbox')).toHaveCount(3)
  await page.getByLabel('选择第 1 条消息').check()
  await page.getByLabel('选择第 3 条消息').check()
  await page.screenshot({ path: 'test-results/chat-history-multi-select.png', fullPage: true })
  page.once('dialog', (dialog) => dialog.accept())
  await page.getByRole('button', { name: '删除所选（2）' }).click()
  await expect.poll(() => deletedIds).toEqual(['message-1', 'message-3'])
  await expect(page.locator('.history-message')).toHaveCount(1)
  await expect(page.getByText('已删除 2 条聊天消息')).toBeVisible()
  await expect(page.getByRole('button', { name: '多选' })).toBeVisible()
  await expectNoHorizontalOverflow(page)
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(reasoning.locator('pre')).toBeVisible()
  await expectNoHorizontalOverflow(page)
  await page.screenshot({ path: 'test-results/chat-history-reasoning-mobile.png', fullPage: true })
})

test('chat history shows historical card names, floor controls, and local message editing', async ({ page }) => {
  await page.addInitScript(() => localStorage.removeItem('catgirl.chat-history.floor-limit.v1'))
  const route = 'qq:90001:private:234567'
  const record = {
    id: 'record-floor-tools',
    channel: 'private',
    external_id: route,
    title: '长聊天记录',
    is_active: true,
    message_count: 129,
    total_tokens: 12900,
    character_name: '欢欢',
    last_message_preview: '第一百二十九层内容',
    created_at: '2026-07-29T06:00:00Z',
    updated_at: '2026-07-29T06:18:00Z',
  }
  const messages = Array.from({ length: 129 }, (_, index) => ({
    id: `floor-message-${index + 1}`,
    conversation_id: record.id,
    position: index,
    role: index % 2 === 0 ? 'user' : 'assistant',
    content: `第${index + 1}层内容 `.repeat(index === 9 ? 80 : 3),
    status: 'complete',
    source: index % 2 === 0 ? 'user' : 'runtime',
    provider_id: null,
    preset_id: 'historical-preset',
    model: index % 2 === 0 ? '' : 'test-model',
    prompt_tokens: null,
    completion_tokens: null,
    total_tokens: null,
    token_count: 100,
    speaker_name: index % 2 === 0 ? '当时的用户卡' : '当时的角色卡',
    message_metadata: {},
    created_at: new Date(Date.UTC(2026, 6, 29, 6, 0, index)).toISOString(),
  }))
  let editedRequest: { messageId: string, content: string } | null = null

  await page.route('**/api/runtime/conversations', async (routeHandler) => {
    await routeHandler.fulfill({ json: [record] })
  })
  await page.route(`**/api/runtime/conversations/${record.id}/messages`, async (routeHandler) => {
    await routeHandler.fulfill({ json: messages })
  })
  await page.route(`**/api/runtime/conversations/${record.id}/messages/floor-message-10`, async (routeHandler) => {
    const body = routeHandler.request().postDataJSON() as { content: string }
    editedRequest = { messageId: 'floor-message-10', content: body.content }
    messages[9] = { ...messages[9], content: body.content, token_count: 12 }
    await routeHandler.fulfill({ json: messages[9] })
  })

  await page.setViewportSize({ width: 1440, height: 760 })
  await page.goto('/#/chat-history')
  const history = page.locator('.history-message-list')
  const floorLimit = page.getByLabel('显示楼层')
  await expect(floorLimit).toHaveValue('100')
  await expect(page.locator('.history-message')).toHaveCount(100)
  await expect(page.locator('.history-message').first().getByText('当时的角色卡')).toBeVisible()
  await expect(page.locator('.history-message').nth(1).getByText('当时的用户卡')).toBeVisible()
  await expect(page.getByText('第 29 层', { exact: true })).toHaveCount(0)
  await expect(page.getByText('第 30 层', { exact: true })).toBeAttached()
  await expect(page.getByText('第 129 层', { exact: true })).toBeInViewport()
  await expect.poll(() => history.evaluate((element) => element.scrollTop)).toBeGreaterThan(0)
  const scrollTopButton = page.getByTitle('滚动到最上方')
  const scrollBottomButton = page.getByTitle('滚动到最下方')
  const [historyBox, scrollTopButtonBox, scrollBottomButtonBox] = await Promise.all([
    history.boundingBox(),
    scrollTopButton.boundingBox(),
    scrollBottomButton.boundingBox(),
  ])
  expect(historyBox).not.toBeNull()
  expect(scrollTopButtonBox).not.toBeNull()
  expect(scrollBottomButtonBox).not.toBeNull()
  expect(Math.abs((historyBox!.x + historyBox!.width - 10) - (scrollTopButtonBox!.x + scrollTopButtonBox!.width))).toBeLessThan(1)
  expect(scrollBottomButtonBox!.x).toBe(scrollTopButtonBox!.x)
  await scrollTopButton.click()
  await expect.poll(() => history.evaluate((element) => Math.round(element.scrollTop))).toBe(0)
  await expect(page.getByText('第 30 层', { exact: true })).toBeInViewport()
  await scrollBottomButton.click()
  await expect.poll(() => history.evaluate((element) => element.scrollTop)).toBeGreaterThan(0)
  await expect(page.getByText('第 129 层', { exact: true })).toBeInViewport()

  await floorLimit.fill('0')
  await floorLimit.blur()
  await expect(page.locator('.history-message')).toHaveCount(129)
  await expect(page.getByText('第 1 层', { exact: true })).toBeAttached()
  await expect(page.getByText('第 129 层', { exact: true })).toBeInViewport()
  await scrollTopButton.click()
  await expect.poll(() => history.evaluate((element) => Math.round(element.scrollTop))).toBe(0)

  const floorTen = page.locator('.history-message').filter({ has: page.getByText('第 10 层', { exact: true }) })
  const originalFloorHeight = await floorTen.evaluate((element) => element.getBoundingClientRect().height)
  await floorTen.getByTitle('编辑第 10 层').click()
  const editor = floorTen.getByLabel('编辑第 10 层文本')
  await expect.poll(() => floorTen.evaluate((element) => element.getBoundingClientRect().height)).toBeGreaterThanOrEqual(originalFloorHeight)
  await expect.poll(() => editor.evaluate((element) => element.getBoundingClientRect().height)).toBeGreaterThan(originalFloorHeight / 2)
  await editor.fill('第十层已在本地修改')
  await page.getByTitle('保存本层').click()
  await expect.poll(() => editedRequest).toEqual({
    messageId: 'floor-message-10',
    content: '第十层已在本地修改',
  })
  await expect(page.getByText('第十层已在本地修改', { exact: true })).toBeVisible()
  await expect(page.getByText('第 10 层已保存', { exact: true })).toBeVisible()
  await expectNoHorizontalOverflow(page)
  await page.screenshot({ path: 'test-results/chat-history-floor-tools.png', fullPage: true })
  await floorLimit.fill('5')
  await floorLimit.blur()
  await expect(page.locator('.history-message')).toHaveCount(5)
  await page.setViewportSize({ width: 390, height: 844 })
  await expectNoHorizontalOverflow(page)
  await page.screenshot({ path: 'test-results/chat-history-floor-tools-mobile.png', fullPage: true })
})

test('large prompt templates scroll without stretching the editor', async ({ page, request }) => {
  const name = `布局回归 ${Date.now()}`
  const createResponse = await request.post('/api/prompt-templates', {
    data: { name, description: '自动测试临时模板' },
  })
  expect(createResponse.ok()).toBeTruthy()
  const template = await createResponse.json() as { id: string; blocks: Array<{ id: string }> }

  try {
    await request.put(`/api/prompt-blocks/${template.blocks[0].id}`, {
      data: {
        title: '运行时宏',
        content: '用户={{user}}；角色={{char}}；消息={{lastUserMessage}}',
      },
    })
    for (let index = 1; index < 30; index += 1) {
      const response = await request.post(`/api/prompt-templates/${template.id}/blocks`, {
        data: { title: `提示词块 ${index + 1}`, role: 'system', content: `预览内容 ${index + 1}`, enabled: true },
      })
      expect(response.ok()).toBeTruthy()
    }

    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/#/prompts')
    await page.getByRole('button', { name: new RegExp(name) }).click()
    await expect(page.locator('.prompt-block-row')).toHaveCount(30)
    await expect(page.locator('.preview-message.kind-template')).toHaveCount(30)
    await expect(page.getByText('未解析：')).toHaveCount(0)

    const layout = await page.evaluate(() => {
      const blocks = document.querySelector('.block-panel .block-list') as HTMLElement
      const preview = document.querySelector('.preview-list') as HTMLElement
      const editor = document.querySelector('.block-editor') as HTMLElement
      const textarea = document.querySelector('.grow-field textarea') as HTMLTextAreaElement
      const firstCard = document.querySelector('.preview-message.kind-template') as HTMLElement
      return {
        blocksScroll: blocks.scrollHeight > blocks.clientHeight,
        previewScroll: preview.scrollHeight > preview.clientHeight,
        textareaBottom: textarea.getBoundingClientRect().bottom,
        editorBottom: editor.getBoundingClientRect().bottom,
        resize: getComputedStyle(textarea).resize,
        firstCardHeight: firstCard.getBoundingClientRect().height,
      }
    })
    expect(layout.blocksScroll).toBeTruthy()
    expect(layout.previewScroll).toBeTruthy()
    expect(layout.textareaBottom).toBeLessThanOrEqual(layout.editorBottom + 1)
    expect(layout.resize).toBe('none')
    expect(layout.firstCardHeight).toBeGreaterThan(45)
    await page.screenshot({ path: 'test-results/prompts-large-template.png', fullPage: true })
  } finally {
    await request.delete(`/api/prompt-templates/${template.id}`)
  }
})

test('prompt blocks can be stashed into the collapsible bar and inserted back', async ({ page, request }) => {
  const name = `折叠栏回归 ${Date.now()}`
  const createResponse = await request.post('/api/prompt-templates', {
    data: { name, description: '自动测试临时模板' },
  })
  expect(createResponse.ok()).toBeTruthy()
  const template = await createResponse.json() as { id: string; blocks: Array<{ id: string }> }

  try {
    await request.put(`/api/prompt-blocks/${template.blocks[0].id}`, {
      data: { title: '正式块', content: '正式内容' },
    })
    const extra = await request.post(`/api/prompt-templates/${template.id}/blocks`, {
      data: { title: '备用块', role: 'system', content: '备用内容', enabled: true },
    })
    expect(extra.ok()).toBeTruthy()

    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/#/prompts')
    await page.getByRole('button', { name: new RegExp(name) }).click()
    await expect(page.locator('.block-list .prompt-block-row')).toHaveCount(2)
    await expect(page.locator('.preview-message.kind-template')).toHaveCount(2)

    const stashToggle = page.getByRole('button', { name: /收纳的提示词/ })
    await expect(stashToggle).toHaveAttribute('aria-expanded', 'false')
    await expect(page.locator('.prompt-stash-list')).toHaveCount(0)

    await page.locator('.block-list .prompt-block-row').nth(1).getByTitle('收进折叠栏').click()
    await expect(page.locator('.block-list .prompt-block-row')).toHaveCount(1)
    await expect(stashToggle).toHaveAttribute('aria-expanded', 'true')
    await expect(page.locator('.prompt-stash-list .prompt-block-row')).toHaveCount(1)
    await expect(page.locator('.preview-message.kind-template')).toHaveCount(1)
    await expect(page.locator('.preview-message.kind-template pre')).toHaveText('正式内容')

    await page.reload()
    await page.getByRole('button', { name: new RegExp(name) }).click()
    await expect(page.locator('.block-list .prompt-block-row')).toHaveCount(1)
    await page.getByRole('button', { name: /收纳的提示词/ }).click()
    await expect(page.locator('.prompt-stash-list .prompt-block-row')).toHaveCount(1)
    await expectNoHorizontalOverflow(page)
    await page.screenshot({ path: 'test-results/prompts-stash-bar.png', fullPage: true })

    await page.getByRole('button', { name: '插入' }).click()
    await expect(page.locator('.block-list .prompt-block-row')).toHaveCount(2)
    await expect(page.locator('.prompt-stash-list .prompt-block-row')).toHaveCount(0)
    await expect(page.locator('.preview-message.kind-template')).toHaveCount(2)
    await expect(page.locator('.preview-message.kind-template pre').nth(1)).toHaveText('备用内容')
  } finally {
    await request.delete(`/api/prompt-templates/${template.id}`)
  }
})
