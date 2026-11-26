import { expect, test } from "vitest";
import { clockText } from "./Working";

test("the clock shows minutes and two-digit seconds, never below zero", () => {
  expect(clockText(0)).toBe("0:00");
  expect(clockText(5.9)).toBe("0:05");
  expect(clockText(74)).toBe("1:14");
  expect(clockText(3725)).toBe("62:05");
  expect(clockText(-3)).toBe("0:00");
});
