import { createLatestRequestManager, isCanceledRequest } from "./latestRequest";

const deferred = () => {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
};

test("only the newest filter request may commit when responses arrive out of order", async () => {
  const manager = createLatestRequestManager();
  const oldResponse = deferred();
  const newResponse = deferred();
  const committed = [];

  const run = async (response) => {
    const request = manager.begin();
    const value = await response.promise;
    if (manager.isCurrent(request.id)) committed.push(value);
  };

  const oldRun = run(oldResponse);
  const newRun = run(newResponse);
  newResponse.resolve("site:last-7-days");
  await newRun;
  oldResponse.resolve("site:full-range");
  await oldRun;

  expect(committed).toEqual(["site:last-7-days"]);
});

test("starting a new request aborts the previous signal and cancel invalidates the active request", () => {
  const manager = createLatestRequestManager();
  const first = manager.begin();
  const second = manager.begin();

  expect(first.signal.aborted).toBe(true);
  expect(manager.isCurrent(first.id)).toBe(false);
  expect(manager.isCurrent(second.id)).toBe(true);

  manager.cancel();
  expect(second.signal.aborted).toBe(true);
  expect(manager.isCurrent(second.id)).toBe(false);
});

test("recognizes axios and browser cancellation errors", () => {
  expect(isCanceledRequest({ code: "ERR_CANCELED" })).toBe(true);
  expect(isCanceledRequest({ name: "AbortError" })).toBe(true);
  expect(isCanceledRequest(new Error("network"))).toBe(false);
});
