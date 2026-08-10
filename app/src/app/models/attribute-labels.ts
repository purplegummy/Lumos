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

// Some categorical attributes have a natural non-alphabetical order (e.g.
// difficulty levels) -- plain alphabetical sort misorders them ("A little" /
// "A lot" / "No" reads as Little, Lot, No). List the intended order here;
// anything not listed falls back to alphabetical in compareCategoryValues.
export const ORDINAL_CATEGORY_ORDER: Record<string, string[]> = {
  difficulty_making_friends: ['No difficulty', 'A little difficulty', 'A lot of difficulty'],
};

export function compareCategoryValues(attr: string, a: string, b: string): number {
  const order = ORDINAL_CATEGORY_ORDER[attr];
  if (order) {
    const ai = order.indexOf(a);
    const bi = order.indexOf(b);
    if (ai !== -1 && bi !== -1) return ai - bi;
  }
  return a < b ? -1 : a > b ? 1 : 0;
}
