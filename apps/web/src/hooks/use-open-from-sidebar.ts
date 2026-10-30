import { useSidebar } from "@/components/ui/sidebar"
import { navigate } from "@/lib/selection"

/** Open a page from the sidebar, closing the sidebar when it is the phone's drawer. */
export function useOpenFromSidebar(): (path: string) => void {
  const { setOpenMobile } = useSidebar()
  return (path) => {
    navigate(path)
    setOpenMobile(false)
  }
}
