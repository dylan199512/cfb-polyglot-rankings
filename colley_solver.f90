program colley_solver
    implicit none
    integer :: n, i, j, ios
    real(8), allocatable :: C(:,:), b(:), r_colley(:)
    real(8), allocatable :: M(:,:), p(:), r_massey(:)

    open(unit=10, file='matrix.dat', status='old', action='read', iostat=ios)
    if (ios /= 0) then
        print *, 'ERROR: Could not open matrix.dat'
        stop 1
    end if

    read(10, *) n
    allocate(C(n, n), b(n), r_colley(n))
    allocate(M(n, n), p(n), r_massey(n))

    ! Read Colley system C * r_colley = b
    do i = 1, n
        read(10, *) (C(i, j), j = 1, n)
    end do
    do i = 1, n
        read(10, *) b(i)
    end do

    ! Read Massey system M * r_massey = p
    do i = 1, n
        read(10, *) (M(i, j), j = 1, n)
    end do
    do i = 1, n
        read(10, *) p(i)
    end do
    close(10)

    call solve_linear_system(n, C, b, r_colley)
    call solve_linear_system(n, M, p, r_massey)

    open(unit=20, file='colley_ratings.dat', status='replace', action='write')
    do i = 1, n
        write(20, '(F16.10, 1X, F16.10)') r_colley(i), r_massey(i)
    end do
    close(20)

    deallocate(C, b, r_colley, M, p, r_massey)
    print '(A, I0, A)', 'FORTRAN COLLEY + MASSEY SOLVER COMPLETE. TEAMS SOLVED: ', n, '.'

contains

    subroutine solve_linear_system(n, A_in, rhs_in, x)
        integer, intent(in) :: n
        real(8), intent(in) :: A_in(n, n), rhs_in(n)
        real(8), intent(out) :: x(n)
        real(8) :: A(n, n), rhs(n), temp_row(n)
        real(8) :: max_val, factor, temp_val, sum_val
        integer :: i, j, k, max_row

        A = A_in
        rhs = rhs_in

        ! Forward elimination with partial pivoting
        do k = 1, n - 1
            max_row = k
            max_val = abs(A(k, k))
            do i = k + 1, n
                if (abs(A(i, k)) > max_val) then
                    max_val = abs(A(i, k))
                    max_row = i
                end if
            end do

            if (max_row /= k) then
                temp_row = A(k, :)
                A(k, :) = A(max_row, :)
                A(max_row, :) = temp_row

                temp_val = rhs(k)
                rhs(k) = rhs(max_row)
                rhs(max_row) = temp_val
            end if

            do i = k + 1, n
                factor = A(i, k) / A(k, k)
                A(i, k:n) = A(i, k:n) - factor * A(k, k:n)
                rhs(i) = rhs(i) - factor * rhs(k)
            end do
        end do

        ! Back substitution
        x(n) = rhs(n) / A(n, n)
        do i = n - 1, 1, -1
            sum_val = 0.0d0
            do j = i + 1, n
                sum_val = sum_val + A(i, j) * x(j)
            end do
            x(i) = (rhs(i) - sum_val) / A(i, i)
        end do
    end subroutine solve_linear_system

end program colley_solver