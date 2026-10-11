/**
 * Keeps request-driven screens from committing an older response after the
 * user has already changed filters. Aborting saves work; the monotonically
 * increasing id is the final guard for clients/servers that cannot abort in
 * time.
 */
export function createLatestRequestManager() {
  let activeId = 0;
  let activeController = null;

  return {
    begin() {
      activeController?.abort();
      activeController = new AbortController();
      activeId += 1;
      return { id: activeId, signal: activeController.signal };
    },
    isCurrent(id) {
      return id === activeId && Boolean(activeController) && !activeController.signal.aborted;
    },
    cancel() {
      activeController?.abort();
      activeController = null;
      activeId += 1;
    },
  };
}

export function isCanceledRequest(error) {
  return error?.code === "ERR_CANCELED" || error?.name === "CanceledError" || error?.name === "AbortError";
}
