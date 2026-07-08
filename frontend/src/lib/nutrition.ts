export const NUTRITION_FIELDS = [
  { key: 'calories', label: '能量', unit: 'kcal', isEnergy: true, upperLimit: false },
  { key: 'protein', label: '蛋白质', unit: 'g', isEnergy: false, upperLimit: false },
  { key: 'fat', label: '脂肪', unit: 'g', isEnergy: false, upperLimit: false },
  { key: 'cholesterol', label: '胆固醇', unit: 'mg', isEnergy: false, upperLimit: true },
  { key: 'carbohydrate', label: '碳水化合物', unit: 'g', isEnergy: false, upperLimit: false },
  { key: 'added_sugar', label: '添加糖', unit: 'g', isEnergy: false, upperLimit: true },
  { key: 'fiber', label: '膳食纤维', unit: 'g', isEnergy: false, upperLimit: false },
  { key: 'sodium', label: '钠', unit: 'mg', isEnergy: false, upperLimit: true },
  { key: 'calcium', label: '钙', unit: 'mg', isEnergy: false, upperLimit: false },
  { key: 'iron', label: '铁', unit: 'mg', isEnergy: false, upperLimit: false },
  { key: 'zinc', label: '锌', unit: 'mg', isEnergy: false, upperLimit: false },
  { key: 'vitamin_a', label: '维生素A', unit: 'ug RAE', isEnergy: false, upperLimit: false },
  { key: 'vitamin_c', label: '维生素C', unit: 'mg', isEnergy: false, upperLimit: false },
  { key: 'vitamin_d', label: '维生素D', unit: 'ug', isEnergy: false, upperLimit: false },
] as const

export type NutritionKey = typeof NUTRITION_FIELDS[number]['key']

export const NUTRITION_KEYS = NUTRITION_FIELDS.map(field => field.key) as NutritionKey[]
export const NUTRITION_LABELS = Object.fromEntries(NUTRITION_FIELDS.map(field => [field.key, field.label])) as Record<string, string>
export const NUTRITION_UNITS = Object.fromEntries(NUTRITION_FIELDS.map(field => [field.key, field.unit])) as Record<string, string>
export const UPPER_LIMIT_NUTRITION_KEYS = new Set<NutritionKey>(
  NUTRITION_FIELDS.filter(field => field.upperLimit).map(field => field.key)
)

export const emptyNutritionValues = (): Record<NutritionKey, string> =>
  Object.fromEntries(NUTRITION_KEYS.map(key => [key, ''])) as Record<NutritionKey, string>
