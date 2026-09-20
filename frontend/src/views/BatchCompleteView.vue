<template>
  <div class="page">
    <div style="display: flex; justify-content: space-between; align-items: center; gap: 12px">
      <div>
        <h1 style="margin-bottom: 4px">批量完成 Run</h1>
        <p class="muted" style="margin-top: 0">
          一次处理多条材料已齐（已有指标与产物）且仍在进行的 Run；逐条提交，单条失败不影响后续
        </p>
      </div>
      <n-button quaternary @click="load">刷新</n-button>
    </div>

    <div class="card" style="margin-bottom: 16px">
      <div style="display: flex; gap: 12px; flex-wrap: wrap; align-items: flex-end">
        <div style="flex: 1; min-width: 280px">
          <n-input
            v-model:value="summary"
            type="textarea"
            :rows="2"
            placeholder="完成摘要（应用到所有勾选的 Run）"
          />
        </div>
        <n-button
          type="success"
          :disabled="!checkedIds.length"
          :loading="submitting"
          @click="submit"
        >
          批量完成（{{ checkedIds.length }}）
        </n-button>
      </div>
    </div>

    <div class="card">
      <n-data-table
        :columns="columns"
        :data="rows"
        :loading="loading"
        :bordered="false"
        :row-key="(row) => row.id"
        :checked-row-keys="checkedIds"
        @update:checked-row-keys="(keys) => (checkedIds = keys)"
      />
    </div>
  </div>
</template>

<script setup>
import { h, onMounted, ref } from 'vue'
import { NTag, useMessage } from 'naive-ui'
import { batchCompleteRuns, listRuns } from '../api/client'

const message = useMessage()
const rows = ref([])
const loading = ref(false)
const submitting = ref(false)
const summary = ref('')
const checkedIds = ref([])

const statusMap = {
  running: { type: 'info', label: '进行中' },
  completed: { type: 'success', label: '已完成' },
  aborted: { type: 'warning', label: '已中止' },
}

const outcomeMap = {
  success: { type: 'success', label: '成功' },
  skipped: { type: 'warning', label: '跳过' },
  failed: { type: 'error', label: '失败' },
}

function hasMetrics(row) {
  return (row.metrics_json || []).length > 0
}

function hasArtifacts(row) {
  return (row.artifacts_json || []).length > 0
}

const columns = [
  { type: 'selection' },
  { title: '项目', key: 'project' },
  { title: '名称', key: 'name' },
  {
    title: '指标',
    key: 'metrics',
    render(row) {
      const n = (row.metrics_json || []).length
      return h(
        NTag,
        { type: n ? 'success' : 'default', size: 'small' },
        { default: () => (n ? `✓ ${n} 条` : '✗ 无') },
      )
    },
  },
  {
    title: '产物',
    key: 'artifacts',
    render(row) {
      const n = (row.artifacts_json || []).length
      return h(
        NTag,
        { type: n ? 'success' : 'default', size: 'small' },
        { default: () => (n ? `✓ ${n} 个` : '✗ 无') },
      )
    },
  },
  {
    title: '材料',
    key: 'ready',
    render(row) {
      const ready = hasMetrics(row) && hasArtifacts(row)
      return h(
        NTag,
        { type: ready ? 'success' : 'warning', size: 'small' },
        { default: () => (ready ? '已齐' : '未齐') },
      )
    },
  },
  { title: '版本', key: 'version', width: 70 },
  {
    title: '状态',
    key: 'status',
    render(row) {
      const m = statusMap[row.status] || { type: 'default', label: row.status }
      return h(NTag, { type: m.type, size: 'small' }, { default: () => m.label })
    },
  },
  {
    title: '结果',
    key: '_outcome',
    render(row) {
      if (!row._outcome) return '—'
      const m = outcomeMap[row._outcome] || { type: 'default', label: row._outcome }
      const children = [
        h(NTag, { type: m.type, size: 'small' }, { default: () => m.label }),
      ]
      if (row._reason) {
        children.push(h('span', { class: 'muted', style: 'font-size: 12px' }, row._reason))
      }
      return h('div', { style: 'display:flex;flex-direction:column;gap:4px' }, children)
    },
  },
]

async function load() {
  loading.value = true
  try {
    rows.value = await listRuns({ status: 'running' })
    checkedIds.value = []
  } catch (e) {
    message.error(e.message || '加载失败')
  } finally {
    loading.value = false
  }
}

async function submit() {
  if (!summary.value.trim()) {
    message.warning('请填写完成摘要')
    return
  }
  if (!checkedIds.value.length) {
    message.warning('请勾选要完成的 Run')
    return
  }
  submitting.value = true
  try {
    const { results } = await batchCompleteRuns({
      run_ids: checkedIds.value,
      result_summary: summary.value.trim(),
    })
    const byId = new Map(results.map((r) => [r.run_id, r]))
    for (const row of rows.value) {
      const r = byId.get(row.id)
      if (!r) continue
      row._outcome = r.outcome
      row._reason = r.reason
      if (r.status) row.status = r.status
      if (r.version != null) row.version = r.version
    }
    const count = (outcome) => results.filter((r) => r.outcome === outcome).length
    const okIds = new Set(results.filter((r) => r.outcome === 'success').map((r) => r.run_id))
    checkedIds.value = checkedIds.value.filter((id) => !okIds.has(id))
    const text = `成功 ${count('success')}，跳过 ${count('skipped')}，失败 ${count('failed')}`
    if (count('failed')) message.error(`批量完成：${text}`)
    else if (count('skipped')) message.warning(`批量完成：${text}`)
    else message.success(`批量完成：${text}`)
  } catch (e) {
    message.error(e.message || '批量完成失败')
  } finally {
    submitting.value = false
  }
}

onMounted(load)
</script>
