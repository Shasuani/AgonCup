import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Button } from "./Button";
import { DataTable, type Column } from "./DataTable";
import { ErrorBanner } from "./ErrorBanner";
import { TextInput } from "./FormField";
import { ConfirmDialog } from "./Modal";
import { ApiError } from "@lol/clients-ts";

describe("Button", () => {
  it("renders and fires onClick", async () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>Go</Button>);
    await userEvent.click(screen.getByRole("button", { name: "Go" }));
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("is disabled + aria-busy when busy", () => {
    render(<Button busy>Go</Button>);
    const btn = screen.getByRole("button", { name: "Go" });
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute("aria-busy", "true");
  });
});

describe("TextInput", () => {
  it("associates the label with the input", () => {
    render(<TextInput label="Team name" />);
    expect(screen.getByLabelText("Team name")).toBeInTheDocument();
  });
});

interface Row {
  id: string;
  name: string;
}

describe("DataTable", () => {
  const columns: Column<Row>[] = [
    { id: "name", header: "Name", cell: (r) => r.name },
  ];

  it("renders rows", () => {
    render(
      <DataTable
        caption="Teams"
        columns={columns}
        rows={[{ id: "1", name: "Alpha" }]}
        rowKey={(r) => r.id}
      />,
    );
    expect(screen.getByText("Alpha")).toBeInTheDocument();
  });

  it("shows the empty message", () => {
    render(
      <DataTable
        caption="Teams"
        columns={columns}
        rows={[]}
        rowKey={(r) => r.id}
        emptyMessage="Nothing here"
      />,
    );
    expect(screen.getByText("Nothing here")).toBeInTheDocument();
  });
});

describe("ErrorBanner", () => {
  it("renders an ApiError detail as text in an alert", () => {
    render(<ErrorBanner error={new ApiError(403, "Forbidden", { detail: "Forbidden" })} />);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Forbidden");
  });

  it("renders nothing for a null error", () => {
    const { container } = render(<ErrorBanner error={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("ConfirmDialog", () => {
  it("renders when open and confirms", async () => {
    const onConfirm = vi.fn();
    render(
      <ConfirmDialog
        open
        title="Delete?"
        message="This cannot be undone."
        onConfirm={onConfirm}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByRole("dialog", { name: "Delete?" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it("renders nothing when closed", () => {
    render(
      <ConfirmDialog
        open={false}
        title="Delete?"
        message="x"
        onConfirm={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
