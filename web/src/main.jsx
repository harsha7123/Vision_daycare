import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";

// No StrictMode: its double-invoked effects would place real phone calls twice.
createRoot(document.getElementById("root")).render(<App />);
