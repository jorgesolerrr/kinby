import type { PageWrite } from "@kinby/contract"
import { cn } from "cn"
import { structuredPatch } from "diff"

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { ViewFrame } from "@/components/view-frame"

/**
 * The page a write asks to draw, as its approval shows it: drawn with its data in the frame a view
 * uses, then its source and what it changes in the page the view holds, a tab away each.
 */
export function PageWriteTabs({ write }: { write: PageWrite }) {
  const codeBlock =
    "max-h-96 overflow-y-auto rounded-lg bg-muted p-3 font-mono text-xs wrap-anywhere whitespace-pre-wrap"
  return (
    <Tabs defaultValue="page">
      <TabsList variant="line">
        <TabsTrigger value="page">Page</TabsTrigger>
        {write.stored_page !== null && <TabsTrigger value="changes">Changes</TabsTrigger>}
        <TabsTrigger value="source">Source</TabsTrigger>
      </TabsList>
      <TabsContent value="page">
        <div className="max-h-96 overflow-y-auto rounded-lg border">
          <ViewFrame title={write.title} page={write.page} data={write.data} />
        </div>
      </TabsContent>
      {write.stored_page !== null && (
        <TabsContent value="changes">
          <pre className={codeBlock}>
            {changes(write.stored_page, write.page).map((line, index) => (
              // A diff repeats lines, and a drawn diff never changes.
              <span
                key={index}
                className={cn(
                  line.startsWith("+") && "text-success",
                  line.startsWith("-") && "text-destructive",
                  line.startsWith("@@") && "text-muted-foreground",
                )}
              >
                {`${line}\n`}
              </span>
            ))}
          </pre>
        </TabsContent>
      )}
      <TabsContent value="source">
        <pre className={codeBlock}>{write.page}</pre>
      </TabsContent>
    </Tabs>
  )
}

/** The lines of the unified diff from `stored` to `page`, each hunk under its header. */
function changes(stored: string, page: string): string[] {
  const { hunks } = structuredPatch("", "", stored, page)
  return hunks.flatMap((hunk) => [
    `@@ -${hunk.oldStart},${hunk.oldLines} +${hunk.newStart},${hunk.newLines} @@`,
    ...hunk.lines,
  ])
}
