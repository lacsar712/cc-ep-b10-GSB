<template>
  <div class="page">
    <div style="display: flex; justify-content: space-between; align-items: center; gap: 12px">
      <div>
        <h1 style="margin-bottom: 4px">批量完成 Run</h1>
        <p class="muted" style="margin-top: 0">
          勾选材料已齐（已有指标与产物）且仍在进行的 Run，逐条提交完成；单条失败不会取消后续
        </p>
      </div>
      <n-button :loading="loading" @click="load">刷新</n-button>
    </div>

    <div class="card" style="margin-bottom: 16px">
      <n-input
        v-model:value="summary"
        type="textarea"
        :rows="2"
        placeholder="完成摘要（应用于本次勾选的所有 Run）"
      />
      <div style="margin-top: 8px; display: flex; gap: 12px; align-items: center; flex-wrap: wrap">
        <n-button
          type="primary"
          :disabled="!checkedIds.length"
          :loading="submitting"
          @click="submit"
        >
          批量完成（已选 {{ checkedIds.length }} 条）
        </n-button>
        <span class="muted">仅处理「进行中」的 Run；缺指标或缺产物会被跳过</span>
      </div>
    </div>

    <div class="card">
      <n-data-table
        v-model:checked-row-keys="checkedIds"
        :columns="columns"
        :data="rows"
        :loading="loading"
        :bordered="false"
        :row-key="(row) => row.id"
      />
      <n-empty v-if="!loading && !rows.length" description="暂无进行中的 Run" style="margin: 24px 0" />
    </div>
  </div>
</template>

<script setup>
import { h, onMounted, ref } from 'vue'
import { NButton, NTag, useMessage } from 'naive-ui'
import { useRouter } from 'vue-router'
import { batchCompleteRuns, listRuns } from '../api/client'

const router = useRouter()
const message = useMessage()
const rows = ref([])
const loading = ref(false)
const submitting = ref(false)
const summary = ref('')
const checkedIds = ref([])

// 与详情页 / 列表页保持一致的状态展示
const statusMap = {
  running: { type: 'info', label: '进行中' },
  completed: { type: 'success', label: '已完成' },
  aborted: { type: 'warning', label: '已中止' },
}

const resultMap = {
  completed: { type: 'success', label: '成功' },
  skipped: { type: 'default', label: '跳过' },
  failed: { type: 'error', label: '失败' },
}

function materialTag(has, label) {
  return h(
    NTag,
    { type: has ? 'success' : 'default', size: 'small' },
    { default: () => (has ? `已有${label}` : `无${label}`) },
  )
}

const columns = [
  { type: 'selection' },
  { title: '项目', key: 'project' },
  { title: '名称', key: 'name' },
  {
    title: '指标',
    key: 'has_metrics',
    width: 90,
    render(row) {
      return materialTag((row.metrics_json || []).length > 0, '指标')
    },
  },
  {
    title: '产物',
    key: 'has_artifacts',
    width: 90,
    render(row) {
      return materialTag((row.artifacts_json || []).length > 0, '产物')
    },
  },
  {
    title: '状态',
    key: 'status',
    width: 90,
    render(row) {
      const m = statusMap[row.status] || { type: 'default', label: row.status }
      return h(NTag, { type: m.type, size: 'small' }, { default: () => m.label })
    },
  },
  { title: '版本', key: 'version', width: 70 },
  {
    title: '结果',
    key: 'result',
    render(row) {
      if (!row._result) return '—'
      const m = resultMap[row._result.status] || { type: 'default', label: row._result.status }
      const children = [
        h(NTag, { type: m.type, size: 'small' }, { default: () => m.label }),
      ]
      if (row._result.reason) {
        children.push(
          h('div', { class: 'muted', style: 'font-size:12px;margin-top:4px' }, row._result.reason),
        )
      }
      return h('div', children)
    },
  },
  {
    title: '操作',
    key: 'actions',
    width: 80,
    render(row) {
      return h(
        NButton,
        { size: 'tiny', onClick: () => router.push(`/runs/${row.id}`) },
        { default: () => '详情' },
      )
    },
  },
]

async function load() {
  loading.value = true
  try {
    const data = await listRuns({ status: 'running' })
    rows.value = data.map((r) => ({ ...r, _result: null }))
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
  submitting.value = true
  try {
    const { results } = await batchCompleteRuns({
      run_ids: checkedIds.value,
      result_summary: summary.value.trim(),
    })
    const byId = new Map(results.map((r) => [r.run_id, r]))
    rows.value = rows.value.map((row) => {
      const r = byId.get(row.id)
      if (!r) return row
      const next = { ...row, _result: { status: r.status, reason: r.reason } }
      if (r.run) {
        // 与各自详情页一致的状态/版本
        next.status = r.run.status
        next.version = r.run.version
        next.finished_at = r.run.finished_at
        next.result_summary = r.run.result_summary
      }
      return next
    })
    const count = (s) => results.filter((r) => r.status === s).length
    const ok = count('completed')
    const skipped = count('skipped')
    const failed = count('failed')
    if (failed) {
      message.error(`完成 ${ok} 条，跳过 ${skipped} 条，失败 ${failed} 条`)
    } else if (skipped) {
      message.warning(`完成 ${ok} 条，跳过 ${skipped} 条`)
    } else {
      message.success(`全部完成：${ok} 条`)
    }
    checkedIds.value = []
  } catch (e) {
    message.error(e.message || '批量完成失败')
  } finally {
    submitting.value = false
  }
}

onMounted(load)
</script>
