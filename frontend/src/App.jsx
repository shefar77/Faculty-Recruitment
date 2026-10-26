import { useCallback, useEffect, useState } from "react";
import { listCandidates, getCandidate } from "./api.js";
import CandidateList from "./CandidateList.jsx";
import CandidateDetail from "./CandidateDetail.jsx";
import UploadPanel from "./UploadPanel.jsx";

export default function App() {
  const [listing, setListing] = useState(null);
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState("");

  const refreshList = useCallback(async () => {
    try {
      setListing(await listCandidates());
    } catch (exc) {
      setError(String(exc.message || exc));
    }
  }, []);

  const refreshDetail = useCallback(async (id) => {
    if (!id) return setDetail(null);
    try {
      setDetail(await getCandidate(id));
    } catch (exc) {
      setError(String(exc.message || exc));
    }
  }, []);

  useEffect(() => { refreshList(); }, [refreshList]);
  useEffect(() => { refreshDetail(selectedId); }, [selectedId, refreshDetail]);

  return (
    <div className="page">
      <header>
        <h1>Faculty Recruitment Screening</h1>
        <p className="muted small">
          DEMO/RESEARCH rubric — not LNMIIT hiring criteria. Publication
          checks are assisted matching, never proof of authorship. Every
          decision remains the reviewer's.
        </p>
      </header>

      {error && <div className="alert alert-danger">{error}</div>}

      <div className="columns">
        <div className="col-left">
          <h2>Candidates</h2>
          <UploadPanel
            onUploaded={async (id) => { await refreshList(); setSelectedId(id); }}
          />
          <CandidateList
            payload={listing}
            selected={selectedId}
            onSelect={setSelectedId}
          />
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
