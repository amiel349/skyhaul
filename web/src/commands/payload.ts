// Command form definitions and client validation. The limits come from the contract schemas
// (contracts/schemas/v1/common.schema.json), never typed in here a second time.
import common from "../../../contracts/schemas/v1/common.schema.json";
import type { DirectCommandType } from "../api/types";

export const COMMAND_TYPES: readonly DirectCommandType[] = [
  "ARM",
  "TAKEOFF",
  "GOTO",
  "RETURN_HOME",
  "LAND",
  "SET_SPEED",
];

export type FieldName = "lat" | "lon" | "altitude_m" | "speed_mps";

export interface FieldSpec {
  name: FieldName;
  label: string;
  unit: string;
  required: boolean;
  min: number;
  max: number;
  /** Lon: the maximum itself is not allowed (180 is written as -180). */
  maxExclusive: boolean;
}

const defs = common.$defs;
const limits: Record<FieldName, Pick<FieldSpec, "min" | "max" | "maxExclusive" | "label" | "unit">> = {
  lat: { label: "Latitude", unit: "deg", min: defs.Lat.minimum, max: defs.Lat.maximum, maxExclusive: false },
  lon: { label: "Longitude", unit: "deg", min: defs.Lon.minimum, max: defs.Lon.exclusiveMaximum, maxExclusive: true },
  altitude_m: {
    label: "Altitude",
    unit: "m",
    min: defs.AltitudeM.minimum,
    max: defs.AltitudeM.maximum,
    maxExclusive: false,
  },
  speed_mps: {
    label: "Speed",
    unit: "m/s",
    min: defs.SpeedMps.minimum,
    max: defs.SpeedMps.maximum,
    maxExclusive: false,
  },
};

function field(name: FieldName, required: boolean, label?: string): FieldSpec {
  const spec = limits[name];
  return { name, required, ...spec, label: label ?? spec.label };
}

/** The payload fields of each command, in form order. Required ones follow the payload schemas. */
export const FIELDS: Record<DirectCommandType, readonly FieldSpec[]> = {
  ARM: [],
  TAKEOFF: [field("altitude_m", true)],
  GOTO: [
    field("lat", true),
    field("lon", true),
    field("altitude_m", true),
    field("speed_mps", false, "Speed (optional)"),
  ],
  RETURN_HOME: [],
  LAND: [],
  SET_SPEED: [field("speed_mps", true)],
};

export type Payload = Partial<Record<FieldName, number>>;
export type FieldErrors = Partial<Record<FieldName, string>>;

export type Validation = { ok: true; payload: Payload } | { ok: false; errors: FieldErrors };

const rangeText = (spec: FieldSpec): string =>
  `Must be ${spec.min} to ${spec.max}${spec.maxExclusive ? ` (not including ${spec.max})` : ""}`;

const outOfRange = (spec: FieldSpec, value: number): boolean =>
  value < spec.min || value > spec.max || (spec.maxExclusive && value >= spec.max);

/** The GoTo target held by the lat and lon inputs, or null while either is empty, not a number or out of range. */
export function parsePoint(values: Partial<Record<FieldName, string>>): { lat: number; lon: number } | null {
  const [latSpec, lonSpec] = FIELDS.GOTO;
  if (!latSpec || !lonSpec) return null;
  const parse = (spec: FieldSpec): number | null => {
    const raw = (values[spec.name] ?? "").trim();
    const value = Number(raw);
    return raw === "" || !Number.isFinite(value) || outOfRange(spec, value) ? null : value;
  };
  const lat = parse(latSpec);
  const lon = parse(lonSpec);
  return lat === null || lon === null ? null : { lat, lon };
}

/** Check what the dispatcher typed (strings, as in the inputs) against the contract limits. */
export function validatePayload(type: DirectCommandType, values: Partial<Record<FieldName, string>>): Validation {
  const errors: FieldErrors = {};
  const payload: Payload = {};
  for (const spec of FIELDS[type]) {
    const raw = (values[spec.name] ?? "").trim();
    if (raw === "") {
      if (spec.required) errors[spec.name] = "Required";
      continue;
    }
    const value = Number(raw);
    if (!Number.isFinite(value)) errors[spec.name] = "Enter a number";
    else if (outOfRange(spec, value)) {
      errors[spec.name] = rangeText(spec);
    } else payload[spec.name] = value;
  }
  return Object.keys(errors).length > 0 ? { ok: false, errors } : { ok: true, payload };
}
