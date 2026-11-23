import { type ContractFields, type FieldKey, type FieldValue, type Note, noteText } from '@contract-ocr/shared-types';

/** A field with its notes in Indonesian, as the Excel and an Indonesian page show them. */
export interface TextField {
  value: FieldValue['value'];
  check: string | null;
  basis: string | null;
}

const text = (note: Note | null) => (note ? noteText(note, 'id') : null);

/** A row with every note rendered in Indonesian, so tests compare the exact wording HR reads. */
export function inIndonesian<T extends { fields: ContractFields }>(row: T): Omit<T, 'fields'> & { fields: Record<FieldKey, TextField> } {
  const fields = Object.fromEntries(
    Object.entries(row.fields).map(([key, field]) => [key, { value: field.value, check: text(field.check), basis: text(field.basis) }]),
  ) as Record<FieldKey, TextField>;
  return { ...row, fields };
}
