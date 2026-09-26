import { useEffect, useState } from "react"

/**
 * What following the latest request reported. A new request forgets the previous one's reports,
 * and `follow` must keep its identity across renders, or it starts over.
 */
export function useFollowing<Request, Report>(
  request: Request | undefined,
  follow: (request: Request, report: (report: Report) => void) => () => void,
): Report | undefined {
  const [followed, setFollowed] = useState<{ request: Request; report: Report }>()
  useEffect(() => {
    if (request === undefined) return
    return follow(request, (report) => setFollowed({ request, report }))
  }, [request, follow])
  return request !== undefined && followed?.request === request ? followed.report : undefined
}
