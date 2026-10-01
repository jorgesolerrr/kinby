import { CircleXIcon } from "lucide-react"
import { Component, type ReactNode } from "react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { reason } from "@/lib/operation"

interface Props {
  /** The URL's path. The page shows again when it changes. */
  path: string
  children: ReactNode
}

/** Why the page at `path` threw while it rendered, if it did. */
interface State {
  path?: string
  failure?: string
}

/**
 * The page under the header, or an error in its place when it throws while it renders, so the
 * sidebar stays usable. React catches a render error only in a class component.
 */
export class PageBoundary extends Component<Props, State> {
  state: State = {}

  static getDerivedStateFromProps({ path }: Props, state: State): State | null {
    return path === state.path ? null : { path, failure: undefined }
  }

  static getDerivedStateFromError(error: unknown): State {
    return { failure: reason(error) }
  }

  render() {
    if (this.state.failure === undefined) return this.props.children
    return (
      <div className="p-6">
        <Alert variant="destructive">
          <CircleXIcon />
          <AlertTitle>This page could not be shown</AlertTitle>
          <AlertDescription>{this.state.failure}</AlertDescription>
        </Alert>
      </div>
    )
  }
}
