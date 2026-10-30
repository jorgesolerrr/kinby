# Local edits to the ui kit

The components in this folder are shadcn's `base-luma` source, installed with the CLI, plus the edits below. A `shadcn add` reinstall overwrites them. Before you reinstall a component, diff it against the registry, carry every edit listed here over, and add any new edit to this file.

## Corners

`--radius` is 10px, so `rounded-sm` is 6px, `rounded-md` 8px, `rounded-lg` 10px and `rounded-xl` 14px. Every corner in the app is one of these four or `rounded-full`, apart from the square and squircle avatar shapes an instance picks. Nothing uses `rounded-2xl`, `rounded-3xl` or `rounded-4xl`; luma's pills are replaced by the ladder below.

| Group             | Radius             | Members                                                                                                                                                                                                                          |
| ----------------- | ------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Controls and rows | `rounded-lg`, 10px | Buttons and inputs, toggles, toggle-group outline, tab list, sidebar menu buttons and sub-buttons, sidebar group label, sidebar skeleton row, items, alerts, empty-state icon                                                    |
| Small controls    | `rounded-md`, 8px  | Dropdown, select and combobox rows, command rows (in and out of a dialog), tab triggers (both orientations), tooltips, sidebar group and menu actions, skeletons, bubble reactions                                               |
| Chips             | `rounded-sm`, 6px  | Badges, combobox chips, kbd hints in input groups, sidebar menu badges                                                                                                                                                           |
| Surfaces          | `rounded-xl`, 14px | Cards (and their header, footer and edge images), dialogs, alert dialogs and their media tile, dropdown, select and combobox popups, the command palette, the user bubble, empty states, the floating and inset sidebar variants |
| Round on purpose  | `rounded-full`     | Avatars, switches and their thumb, status dots                                                                                                                                                                                   |

- Buttons, inputs, textareas, input groups, select triggers and choice-card labels take `rounded-lg`. The xs button and xs icon button take `rounded-md`, so they don't turn back into pills at 24px.
- The command palette and its dialog take `rounded-xl` where luma has `rounded-4xl`, and its rows `rounded-md` where luma has `rounded-2xl`, and `rounded-3xl` in a dialog.
- The sidebar header and content don't raise `--radius` to the `xl` value, as luma's source does. Sidebar rows use the app's `--radius` like every other control.
- Pages outside the kit take their corners from the kit and don't restyle them by hand. The one exception is the status dots, which stay round.

## Variants and behaviour

- **avatar**: the `circle`, `squircle` and `square` shapes, and the colour variants blue, violet, green, amber, red and gray, drawn from the `--avatar-*` tokens.
- **badge**: the `success` and `attention` variants. Attention fills with ember.
- **button**: the `aria-disabled` state looks and acts like `disabled`.
- **chart**: the inline-style lint waiver on the series style, the legend swatch drawn from a CSS variable, and tooltip numbers formatted in the `en` locale.
- **command**: the row and its shortcut take the selected styles from `data-[selected=true]`, not from the bare `data-selected` variant. cmdk writes `data-selected="false"` on every row it hasn't selected, and the bare variant matches the attribute's presence, so every row looked selected.
- **dialog**: the content's `lg` size, a wide and tall flex column with no padding or gap, for a dialog that lays out its own body, as Settings does.
- **empty**: the title at `text-base`, on the section title scale.
- **input-group**: the textarea's `prompt` variant, and an addon click focuses a textarea as well as an input.
- **item**: the description's `lines` variant.
- **marker**: `tabular-nums`, and the `destructive` variant.
- **message-scroller**: the item has no `content-visibility` (#377).
- **sidebar**: the softer hover on menu buttons, the selected row's icon in the sidebar primary (kin), and the action and badge centred on the lg menu button at `top-4.5`.
- **tabs**: the root passes `orientation` on to Base UI, so a vertical tab list reads and moves with the arrow keys as vertical. The list and its triggers take their orientation styles from their own `data-orientation` rather than from a `group/tabs` ancestor, so horizontal tabs inside a vertical tabs panel, as the Usage charts in Settings, stay horizontal.
