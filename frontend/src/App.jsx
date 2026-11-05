import { useCallback, useEffect, useRef, useState } from "react";
import { listCandidates, getCandidate } from "./api.js";
import CandidateList from "./CandidateList.jsx";
import CandidateDetail from "./CandidateDetail.jsx";
import UploadPanel from "./UploadPanel.jsx";
import { useToast } from "./toast.jsx";
import { loadFailed, serverRestored } from "./notices.js";

export default function App() {
  const toast = useToast();
  const [listing, setListing] = useState(null);
  const [listError, setListError] = useState(null);
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);

  // Set when a request finds the server unreachable, so the next request
  // that succeeds can say the connection is back. Without it, an outage
  // toast would be the last word even after the server recovered.
  const serverDown = useRef(false);

  const noteSuccess = useCallback(() => {
    if (!serverDown.current) return;
    serverDown.current = false;
    toast.show(serverRestored());
  }, [toast]);

  const noteFailure = useCallback((exc, what) => {
    if (exc?.kind === "unreachable") serverDown.current = true;
    toast.show(loadFailed(exc, what));
  }, [toast]);

  const refreshList = useCallback(async () => {
    try {
      setListing(await listCandidates());
      setListError(null);
      noteSuccess();
    } catch (exc) {
      // A list that loaded before stays on screen: stale figures with a
      // toast saying the refresh failed beat a blank column.
      setListError(exc);
      noteFailure(exc, "the candidate list");
    }
  }, [noteSuccess, noteFailure]);

  const refreshDetail = useCallback(async (id) => {
    if (!id) return setDetail(null);
    try {
      setDetail(await getCandidate(id));
      noteSuccess();
    } catch (exc) {
      noteFailure(exc, "this candidate's record");
      if (exc?.status === 404) {
        setSelectedId(null);
        setDetail(null);
        refreshList();
      }
    }
  }, [noteSuccess, noteFailure, refreshList]);

  useEffect(() => { refreshList(); }, [refreshList]);
  useEffect(() => { refreshDetail(selectedId); }, [selectedId, refreshDetail]);

  return (
    <div className="page">
      <header>
        <h1>Faculty Recruitment Screening</h1>
      </header>

      <div className="columns">
        <div className="col-left">
          <h2>Candidates</h2>
          <UploadPanel
            onUploaded={async (id) => { await refreshList(); setSelectedId(id); }}
          />
          {!listing && listError ? (
            <div className="card load-failed" role="status">
              <p>
                The candidate list couldn't be loaded.
                <span className="muted small"> The server may be offline.</span>
              </p>
              <button type="button" onClick={refreshList}>Try again</button>
            </div>
          ) : (
            <CandidateList
              payload={listing}
              selected={selectedId}
              onSelect={setSelectedId}
            />
          )}
        </div>
        <div className="col-right">
          <CandidateDetail
            candidate={detail}
            onOverridden={async () => {
              await refreshDetail(selectedId);
              await refreshList();
            }}
          />
        </div>
      </div>
    </div>
  );
}
