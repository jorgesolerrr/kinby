import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogMedia,
  AlertDialogTitle,
  AlertDialogTrigger,
  Button,
} from "@kinby/web"
import { ArchiveIcon, Trash2Icon } from "lucide-react"

export const DeleteSecret = () => (
  <AlertDialog open>
    <AlertDialogTrigger render={<Button variant="outline" size="sm" />}>
      <Trash2Icon data-icon="inline-start" />
      Delete
    </AlertDialogTrigger>
    <AlertDialogContent>
      <AlertDialogHeader>
        <AlertDialogTitle>Delete GITHUB_TOKEN?</AlertDialogTitle>
        <AlertDialogDescription>
          The hub deletes its copy. A token stays valid at its provider. It unlinks research and
          inbox, which keep the value they hold.
        </AlertDialogDescription>
      </AlertDialogHeader>
      <AlertDialogFooter>
        <AlertDialogCancel>Cancel</AlertDialogCancel>
        <AlertDialogAction variant="destructive">Delete</AlertDialogAction>
      </AlertDialogFooter>
    </AlertDialogContent>
  </AlertDialog>
)

export const WithMedia = () => (
  <AlertDialog open>
    <AlertDialogContent>
      <AlertDialogHeader>
        <AlertDialogMedia>
          <ArchiveIcon />
        </AlertDialogMedia>
        <AlertDialogTitle>Remove the research instance?</AlertDialogTitle>
        <AlertDialogDescription>
          It stops and moves to Removed instances. Its threads and memory stay on disk until you
          purge it.
        </AlertDialogDescription>
      </AlertDialogHeader>
      <AlertDialogFooter>
        <AlertDialogCancel>Keep it</AlertDialogCancel>
        <AlertDialogAction variant="destructive">Remove instance</AlertDialogAction>
      </AlertDialogFooter>
    </AlertDialogContent>
  </AlertDialog>
)

export const Small = () => (
  <AlertDialog open>
    <AlertDialogContent size="sm">
      <AlertDialogHeader>
        <AlertDialogTitle>Delete morning-brief?</AlertDialogTitle>
        <AlertDialogDescription>
          This removes routines/morning-brief and every file in it.
        </AlertDialogDescription>
      </AlertDialogHeader>
      <AlertDialogFooter>
        <AlertDialogCancel>Cancel</AlertDialogCancel>
        <AlertDialogAction variant="destructive">Delete</AlertDialogAction>
      </AlertDialogFooter>
    </AlertDialogContent>
  </AlertDialog>
)
