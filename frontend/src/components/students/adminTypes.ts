export type ClassNode = {
  id: number
  name: string
  grade_id: number
  is_active: boolean
  student_count?: number
}

export type GradeNode = {
  id: number
  name: string
  stage_id: number
  is_active: boolean
  classes: ClassNode[]
}

export type StageNode = {
  id: number
  name: string
  campus_id: number
  stage_type?: string | null
  is_active: boolean
  grades: GradeNode[]
}

export type CampusNode = {
  id: number
  name: string
  school_id: number
  is_active: boolean
  stages: StageNode[]
}

export type SchoolNode = {
  id: number
  name: string
  is_active: boolean
  campuses: CampusNode[]
}

export type Student = {
  id: number
  student_no: string
  registration_no?: string | null
  name: string
  class_id: number | null
  class_name: string | null
  grade_name: string | null
  card_no: string | null
  gender?: string | null
  source: string | null
  enrollment_status: 'enrolled' | 'graduated'
  is_active: boolean
  is_locally_disabled: boolean
  dingtalk_user_id: string | null
}

export type Guardian = {
  id: number
  name: string
  relation: string | null
  phone: string | null
  dingtalk_user_id: string | null
  user_id: number | null
}

export type ClassOption = {
  id: number
  label: string
  isActive: boolean
  gradeId: number
}

export function classOptions(tree: SchoolNode[], includeArchived = false): ClassOption[] {
  const result: ClassOption[] = []
  for (const school of tree) {
    for (const campus of school.campuses) {
      for (const stage of campus.stages) {
        for (const grade of stage.grades) {
          for (const classNode of grade.classes) {
            const active = school.is_active && campus.is_active && stage.is_active && grade.is_active && classNode.is_active
            if (includeArchived || active) {
              result.push({
                id: classNode.id,
                label: `${school.name} / ${campus.name} / ${stage.name} / ${grade.name} / ${classNode.name}`,
                isActive: active,
                gradeId: grade.id,
              })
            }
          }
        }
      }
    }
  }
  return result
}
