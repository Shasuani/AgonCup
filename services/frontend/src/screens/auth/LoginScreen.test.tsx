import { describe, expect, it } from "vitest";
import { act, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AppProviders } from "../../App";
import LoginScreen from "./LoginScreen";

async function renderLanding() {
  const view = render(
    <AppProviders>
      <MemoryRouter initialEntries={["/login"]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <LoginScreen />
      </MemoryRouter>
    </AppProviders>,
  );
  // Let the session-restore probe settle so its state update lands inside act().
  await act(async () => {});
  return view;
}

describe("LoginScreen (public landing page)", () => {
  it("labels the page with the hero heading", async () => {
    await renderLanding();
    expect(screen.getByRole("main", { name: /Run League of Legends tournaments/i })).toBeInTheDocument();
  });

  it("keeps exactly one 'Sign in with Discord' button, inside the sign-in card", async () => {
    const { container } = await renderLanding();
    const btn = screen.getByRole("button", { name: /Sign in with Discord/i });
    expect(container.querySelector("[data-login-card]")).toContainElement(btn);
    expect(btn).toHaveAttribute("data-login-cta");
  });

  it("shows the trailer with its music credit", async () => {
    const { container } = await renderLanding();
    const video = container.querySelector("video");
    expect(video).toHaveAttribute("src", "/media/agoncup-trailer.mp4");
    expect(video).toHaveAttribute("poster", "/media/agoncup-trailer-poster.webp");
    expect(screen.getAllByText(/Kevin MacLeod/).length).toBeGreaterThan(0);
  });

  it("links both desktop installers", async () => {
    await renderLanding();
    const start = document.getElementById("get-started")!;
    expect(within(start).getByRole("link", { name: /Download for Windows/i })).toHaveAttribute(
      "href",
      "/downloads/AgonCup-Setup.exe",
    );
    expect(within(start).getByRole("link", { name: /Download for Mac/i })).toHaveAttribute(
      "href",
      "/downloads/AgonCup.dmg",
    );
  });

  it("shows every feature with a described screenshot", async () => {
    await renderLanding();
    const features = document.getElementById("features")!;
    const imgs = within(features).getAllByRole("img");
    expect(imgs).toHaveLength(5);
    for (const img of imgs) expect(img.getAttribute("alt")?.length).toBeGreaterThan(20);
  });

  it("carries the Riot Games legal notice", async () => {
    await renderLanding();
    expect(screen.getByText(/isn't endorsed by Riot Games/i)).toBeInTheDocument();
  });
});
