import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import {
  DownloadClient,
  detectDesktopOs,
  MAC_INSTALLER_URL,
  WINDOWS_INSTALLER_URL,
} from "./DownloadClient";

describe("detectDesktopOs", () => {
  it("recognises Windows and macOS user agents, else other", () => {
    expect(detectDesktopOs("Mozilla/5.0 (Windows NT 10.0; Win64; x64)")).toBe("windows");
    expect(detectDesktopOs("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)")).toBe("mac");
    expect(detectDesktopOs("Mozilla/5.0 (X11; Linux x86_64)")).toBe("other");
    expect(detectDesktopOs("")).toBe("other");
  });
});

describe("DownloadClient", () => {
  it("links both installers at their stable same-origin paths", () => {
    render(<DownloadClient os="other" />);
    const win = screen.getByRole("link", { name: "Download for Windows" });
    const mac = screen.getByRole("link", { name: "Download for Mac" });
    expect(win).toHaveAttribute("href", WINDOWS_INSTALLER_URL);
    expect(mac).toHaveAttribute("href", MAC_INSTALLER_URL);
    expect(WINDOWS_INSTALLER_URL).toBe("/downloads/LeagueOfLeagues-Setup.exe");
    expect(MAC_INSTALLER_URL).toBe("/downloads/LeagueOfLeagues.dmg");
    // Neither is ever hidden; an unknown OS just gets no highlight.
    expect(win).not.toHaveAttribute("data-recommended");
    expect(mac).not.toHaveAttribute("data-recommended");
    expect(screen.queryByText("For this device")).not.toBeInTheDocument();
  });

  it("highlights the visitor's own OS", () => {
    render(<DownloadClient os="mac" />);
    expect(screen.getByRole("link", { name: "Download for Mac" })).toHaveAttribute(
      "data-recommended",
    );
    expect(screen.getByRole("link", { name: "Download for Windows" })).not.toHaveAttribute(
      "data-recommended",
    );
    expect(screen.getByText("For this device")).toBeInTheDocument();
  });
});
