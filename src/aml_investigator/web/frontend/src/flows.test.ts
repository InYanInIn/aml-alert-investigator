import { expect, test } from "vitest";
import { aggregateFlows, flowCurve } from "./flows";

test("the overview collapses external counterparties of the same kind", () => {
  expect(aggregateFlows(
    [
      { from_client: "a", to_client: "external:merchant", count: 2, eur: 30 },
      { from_client: "b", to_client: "external:merchant", count: 4, eur: 55 },
    ],
    new Map([["a", "worker"], ["b", "worker"]]),
  )).toEqual([{ from_client: "worker", to_client: "external:merchant", count: 6, eur: 85 }]);
});

test("signed adjustments reduce a flow without merging opposing directions", () => {
  expect(aggregateFlows([
    { from_client: "a", to_client: "b", count: 1, eur: 50 },
    { from_client: "a", to_client: "b", count: 1, eur: -75 },
    { from_client: "b", to_client: "a", count: 1, eur: 12 },
  ])).toEqual([
    { from_client: "a", to_client: "b", count: 2, eur: -25 },
    { from_client: "b", to_client: "a", count: 1, eur: 12 },
  ]);
});

test("an empty window produces no arcs", () => {
  expect(aggregateFlows([])).toEqual([]);
});

test("reciprocal flows approach their recipients on opposite sides", () => {
  const eastbound = flowCurve([-60, 0], [60, 0]);
  const westbound = flowCurve([60, 0], [-60, 0]);

  expect(eastbound.end[0]).toBeGreaterThan(54);
  expect(westbound.end[0]).toBeLessThan(-54);
  expect(eastbound.end[1]).toBeGreaterThan(0);
  expect(westbound.end[1]).toBeLessThan(0);
  expect(eastbound.path).toMatch(/^M.+ C/);
});

test("an internal transfer has a visible loop without covering the client node", () => {
  const curve = flowCurve([0, -100], [0, -100]);

  expect(curve.end).toEqual([-4, -100]);
  expect(curve.path).toContain("-122");
  expect(curve.path).not.toContain("NaN");
});
