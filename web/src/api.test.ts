// Offline: fetch is replaced by a fake, so these never reach a server or cost anything.
import { describe, expect, it } from "vitest";

import { ApiError, NetworkError, createApi, friendlyError, tokenStore } from "./api";

type Call = { url: string; init: RequestInit };

function fakeFetch(status: number, body: unknown, calls: Call[] = []): typeof fetch {
  return (async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;
}

describe("createApi", () => {
  it("sends the token and the posting", async () => {
    const calls: Call[] = [];
    const api = createApi("https://api.example", "secret", fakeFetch(200, { id: 1, decision: "apply" }, calls));
    const result = await api.createCheck("Python required");
    expect(result.decision).toBe("apply");
    expect(calls[0].url).toBe("https://api.example/checks");
    expect(calls[0].init.method).toBe("POST");
    expect((calls[0].init.headers as Record<string, string>).Authorization).toBe("Bearer secret");
    expect(JSON.parse(calls[0].init.body as string)).toEqual({ posting: "Python required" });
  });

  it("sends no body or content type on a GET", async () => {
    const calls: Call[] = [];
    await createApi("https://api.example", "secret", fakeFetch(200, { ok: true }, calls)).authCheck();
    expect(calls[0].init.body).toBeUndefined();
    expect(calls[0].init.headers).not.toHaveProperty("Content-Type");
  });

  it("turns an error status into an ApiError with the server's detail", async () => {
    const api = createApi("https://api.example", "secret", fakeFetch(429, { detail: "Limit reached: $0.50 today." }));
    await expect(api.createCheck("x")).rejects.toEqual(new ApiError(429, "Limit reached: $0.50 today."));
  });

  it("turns a failed request into a NetworkError", async () => {
    const offline = (async () => {
      throw new TypeError("Failed to fetch");
    }) as typeof fetch;
    await expect(createApi("https://api.example", "s", offline).authCheck()).rejects.toBeInstanceOf(NetworkError);
  });
});

describe("friendlyError", () => {
  it.each([
    [new ApiError(401, "missing or wrong token"), "Your token was refused. Log out and enter it again."],
    [new ApiError(409, "upload first: PUT /resume"), "Upload your resume on the Resume tab first."],
    [
      new ApiError(409, "upload first: PUT /profile"),
      "The server has no profile yet (grad dates, work authorization). Upload profile.yaml (PUT /profile).",
    ],
    [new ApiError(429, "Limit reached: $0.50 today."), "Spending limit reached: Limit reached: $0.50 today."],
    [new ApiError(422, "posting is empty"), "posting is empty"],
    [new ApiError(500, "boom"), "The server had a problem (500). Try again in a minute."],
    [new NetworkError("x"), "Can't reach the server. Check your connection and try again."],
    [new Error("?"), "Something went wrong. Try again."],
  ])("explains %s", (error, sentence) => {
    expect(friendlyError(error)).toBe(sentence);
  });
});

describe("tokenStore", () => {
  it("keeps, returns, and forgets the token", () => {
    const data = new Map<string, string>();
    const storage = {
      getItem: (k: string) => data.get(k) ?? null,
      setItem: (k: string, v: string) => void data.set(k, v),
      removeItem: (k: string) => void data.delete(k),
    } as Storage;
    expect(tokenStore.get(storage)).toBeNull();
    tokenStore.set("abc", storage);
    expect(tokenStore.get(storage)).toBe("abc");
    tokenStore.clear(storage);
    expect(tokenStore.get(storage)).toBeNull();
  });
});

describe("the history and leaderboard calls", () => {
  it.each([
    ["listChecks", [], "GET", "/checks"],
    ["getCheck", [7], "GET", "/checks/7"],
    ["leaderboard", [], "GET", "/leaderboard"],
    ["markDone", ["machine learning"], "PUT", "/done/machine%20learning"],
    ["unmarkDone", ["c++"], "DELETE", "/done/c%2B%2B"],
    ["markApplied", [7], "PUT", "/checks/7/applied"],
    ["unmarkApplied", [7], "DELETE", "/checks/7/applied"],
  ] as const)("%s calls %s %s", async (name, args, method, path) => {
    const calls: Call[] = [];
    const api = createApi("https://api.example", "secret", fakeFetch(200, {}, calls));
    await (api[name] as (...a: unknown[]) => Promise<unknown>)(...args);
    expect(calls[0].init.method).toBe(method);
    expect(calls[0].url).toBe(`https://api.example${path}`);
  });
});

describe("the resume calls", () => {
  it("uploads the file as a form, letting the browser set the content type", async () => {
    const calls: Call[] = [];
    const api = createApi("https://api.example", "secret", fakeFetch(200, { draft: "x" }, calls));
    await api.importResume(new File(["hello"], "me.pdf"));
    expect(calls[0].url).toBe("https://api.example/resume/import");
    expect(calls[0].init.headers).not.toHaveProperty("Content-Type");
    const sent = calls[0].init.body as FormData;
    expect((sent.get("file") as File).name).toBe("me.pdf");
  });

  it("saves the resume as raw text, not JSON", async () => {
    const calls: Call[] = [];
    const api = createApi("https://api.example", "secret", fakeFetch(200, { version: "v2" }, calls));
    expect((await api.saveResume("version: v2\n")).version).toBe("v2");
    expect(calls[0].init.method).toBe("PUT");
    expect(calls[0].init.body).toBe("version: v2\n");
    expect((calls[0].init.headers as Record<string, string>)["Content-Type"]).toBe("text/plain; charset=utf-8");
  });

  it("reads the saved resume as text", async () => {
    const text = (async () => new Response("version: v1\n", { status: 200 })) as unknown as typeof fetch;
    expect(await createApi("https://api.example", "s", text).savedResume()).toBe("version: v1\n");
  });

  it.each([
    ["recheckPlan", "GET"],
    ["recheck", "POST"],
  ] as const)("%s calls %s /recheck", async (name, method) => {
    const calls: Call[] = [];
    await createApi("https://api.example", "secret", fakeFetch(200, {}, calls))[name]();
    expect(calls[0].init.method).toBe(method);
    expect(calls[0].url).toBe("https://api.example/recheck");
  });

  it.each([413, 415])("shows the server's own reason for a refused file (%i)", (status) => {
    expect(friendlyError(new ApiError(status, "Upload a PDF, DOCX, or TXT file."))).toBe(
      "Upload a PDF, DOCX, or TXT file.",
    );
  });
});

describe("the matches calls", () => {
  it.each([
    ["matches", [], "GET", "/matches"],
    ["runMatches", [], "POST", "/matches/run"],
    ["settings", [], "GET", "/settings"],
    ["saveSettings", [["Software"]], "PUT", "/settings"],
    ["quota", [], "GET", "/checks/quota"],
    ["similar", ["Data Intern"], "POST", "/checks/similar"],
  ] as const)("%s calls %s %s", async (name, args, method, path) => {
    const calls: Call[] = [];
    const api = createApi("https://api.example", "secret", fakeFetch(200, {}, calls));
    await (api[name] as (...a: unknown[]) => Promise<unknown>)(...args);
    expect(calls[0].init.method).toBe(method);
    expect(calls[0].url).toBe(`https://api.example${path}`);
  });
});
