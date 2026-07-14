import { useState } from 'react'
import toast from 'react-hot-toast'
import { studentApi } from '@/api/client'
import { AdminDialogShell, fieldClassName, primaryButtonClassName, secondaryButtonClassName } from './AdminDialogShell'
import type { ClassOption, Student } from './adminTypes'

export function StudentEditorDialog({ student, classes, onClose, onSaved }: {
  student: Student | null
  classes: ClassOption[]
  onClose: () => void
  onSaved: () => void
}) {
  const [form, setForm] = useState(() => ({
    student_no: student?.student_no || '',
    name: student?.name || '',
    class_id: student?.class_id ? String(student.class_id) : '',
    card_no: student?.card_no || '',
    gender: student?.gender || '',
    enrollment_status: student?.enrollment_status || 'enrolled',
    is_locally_disabled: student?.is_locally_disabled || false,
  }))
  const [saving, setSaving] = useState(false)
  const selectedClassIsActive = classes.some(option => option.id === Number(form.class_id))

  const save = async () => {
    if (!form.student_no.trim() || !form.name.trim() || (form.enrollment_status === 'enrolled' && !selectedClassIsActive)) {
      toast.error('请填写学号、姓名，并为在校学生选择有效班级')
      return
    }
    setSaving(true)
    try {
      const payload: Record<string, any> = {
        ...form,
        student_no: form.student_no.trim(),
        name: form.name.trim(),
        card_no: form.card_no.trim() || null,
        gender: form.gender.trim() || null,
      }
      if (selectedClassIsActive) payload.class_id = Number(form.class_id)
      if (student) await studentApi.update(student.id, payload)
      else await studentApi.create(payload)
      toast.success(student ? '学生信息已保存' : '学生已添加')
      onSaved()
    } finally {
      setSaving(false)
    }
  }

  return (
    <AdminDialogShell
      title={student ? `编辑 ${student.name}` : '添加学生'}
      description="学号用于匹配导入数据；调班不会改变已有消费、识别和报告记录。"
      onClose={onClose}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <label className="space-y-1.5 text-sm">
          <span>学号 *</span>
          <input className={fieldClassName} value={form.student_no} onChange={event => setForm(current => ({ ...current, student_no: event.target.value }))} />
        </label>
        <label className="space-y-1.5 text-sm">
          <span>姓名 *</span>
          <input className={fieldClassName} value={form.name} onChange={event => setForm(current => ({ ...current, name: event.target.value }))} />
        </label>
        <label className="space-y-1.5 text-sm sm:col-span-2">
          <span>当前班级 *</span>
          <select className={fieldClassName} value={form.class_id} onChange={event => setForm(current => ({ ...current, class_id: event.target.value }))}>
            <option value="">请选择班级</option>
            {student?.class_id && !selectedClassIsActive ? <option value={student.class_id}>{student.class_name || '原班级'}（已归档）</option> : null}
            {classes.map(option => <option key={option.id} value={option.id}>{option.label}</option>)}
          </select>
        </label>
        <label className="space-y-1.5 text-sm">
          <span>消费卡号</span>
          <input className={fieldClassName} value={form.card_no} onChange={event => setForm(current => ({ ...current, card_no: event.target.value }))} />
        </label>
        <label className="space-y-1.5 text-sm">
          <span>性别</span>
          <input className={fieldClassName} value={form.gender} onChange={event => setForm(current => ({ ...current, gender: event.target.value }))} placeholder="男 / 女" />
        </label>
        {student ? (
          <label className="space-y-1.5 text-sm">
            <span>在校状态</span>
            <select className={fieldClassName} value={form.enrollment_status} onChange={event => setForm(current => ({ ...current, enrollment_status: event.target.value as 'enrolled' | 'graduated' }))}>
              <option value="enrolled">在校</option>
              <option value="graduated">已毕业</option>
            </select>
          </label>
        ) : null}
        <label className="flex items-center gap-2 self-end rounded-md border border-border px-3 py-2 text-sm">
          <input type="checkbox" checked={form.is_locally_disabled} onChange={event => setForm(current => ({ ...current, is_locally_disabled: event.target.checked }))} />
          临时停用学生
        </label>
      </div>
      <div className="mt-6 flex justify-end gap-2">
        <button type="button" className={secondaryButtonClassName} onClick={onClose}>取消</button>
        <button type="button" className={primaryButtonClassName} disabled={saving} onClick={save}>{saving ? '保存中…' : '保存学生'}</button>
      </div>
    </AdminDialogShell>
  )
}
