// Shared attribute display-name lookup. Originally lived only inside the
// elicitation modal; now also used on the main CONTROL page (filters,
// encoding dropdowns, details table) so attribute names read the same way
// everywhere instead of showing raw snake_case only in the modal.
export const ATTR_LABELS: Record<string, string> = {
  child_age_years:               'Age (years)',
  child_sex:                     'Sex',
  screen_time_weekday:           'Daily Screen Time (hours)',
  hours_sleep_weeknight:         'Sleep Hours (weeknight)',
  days_physical_activity_week:   'Physical Activity (days/week)',
  difficulty_making_friends:     'Difficulty Making Friends',
  ever_diagnosed_depression:     'Diagnosed with Depression',
  ever_diagnosed_anxiety:        'Diagnosed with Anxiety',
  ever_diagnosed_dep_or_anx:     'Diagnosed with Depression or Anxiety',
};

export function cleanAttr(attr: string): string {
  return ATTR_LABELS[attr] ?? attr.replace(/_/g, ' ');
}
