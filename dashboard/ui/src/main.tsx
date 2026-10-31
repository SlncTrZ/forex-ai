import React from "react";
import { createRoot } from "react-dom/client";
import { LiveCockpit } from "./LiveCockpit";
import "./theme.css";
import "./live-cockpit.css";
createRoot(document.getElementById("root")!).render(<React.StrictMode><LiveCockpit/></React.StrictMode>);
